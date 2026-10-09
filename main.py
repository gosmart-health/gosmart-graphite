import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import Receive, Scope, Send

from actor import get_audit_actor, get_dicom_actor, stop_audit_actor, stop_dicom_actor
from api.v1.router import api_v1_router
from core.config import get_settings
from core.logging import bind_log_context, clear_log_context, get_logger, setup_logging
from db.neo4j import neo4j_client
from service.graph_service import graph_service

logger = get_logger("gosmart_gs.server")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan for managing background services and DB drivers."""
    setup_logging()
    settings = get_settings()
    logger.info(
        "server_starting",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        log_rotation_days=settings.LOG_ROTATION_DAYS,
    )
    logger.info("storage_configured", storage_root=settings.STORAGE_ROOT_URI)
    logger.info("db_configured", db_connection=settings.DB_CONNECTION_URI)

    # Initialize Neo4j Driver & Constraints
    try:
        await neo4j_client.connect()
        await graph_service.init_schema_constraints()
        from service.system_service import system_service

        await system_service.bootstrap_system()
    except Exception as e:
        logger.warning("neo4j_init_notice", error=str(e))

    # Initialize Pykka background actors
    audit_actor_ref = get_audit_actor()
    logger.info("audit_actor_started", actor_urn=audit_actor_ref.actor_urn)
    actor_ref = get_dicom_actor()
    logger.info("dicom_actor_started", actor_urn=actor_ref.actor_urn)

    # Bootstrap initial Platform Administrator if configured
    try:
        from service.user_service import user_service

        await user_service.bootstrap_admin_user()
    except Exception as e:
        logger.warning("admin_bootstrap_failed_notice", error=str(e))

    # Initialize DICOM SCP Servers for active DICOM configurations
    if getattr(settings, "DICOM_SCP_ENABLED", True):
        try:
            from service.pynetdicom_service import pynetdicom_service
            from service.system_service import system_service

            active_configs = await system_service.list_configurations(role="SCP")
            if not active_configs:
                scp_host = getattr(settings, "DICOM_SCP_HOST", "0.0.0.0")
                scp_port = getattr(settings, "DICOM_SCP_PORT", 11112)
                scp_aet = getattr(settings, "DICOM_AE_TITLE", "GOSMART_GS")
                active_configs = [
                    {
                        "id": f"default_scp_{scp_port}",
                        "ae_title": scp_aet,
                        "roles": ["SCP"],
                        "host": scp_host,
                        "port": scp_port,
                        "is_promiscuous": True,
                        "dicom_services": ["*"],
                        "transfer_syntaxes": ["*"],
                    }
                ]
            pynetdicom_service.sync_from_configurations(active_configs)
        except Exception as e:
            logger.error("dicom_scp_start_failed", error=str(e), exc_info=True)

    yield

    # Clean shutdown
    logger.info("server_shutting_down", app_name=settings.APP_NAME)
    if getattr(settings, "DICOM_SCP_ENABLED", True):
        try:
            from service.pynetdicom_service import pynetdicom_service

            pynetdicom_service.stop_all()
        except Exception as e:
            logger.warning("dicom_scp_stop_failed", error=str(e))
    stop_dicom_actor()
    stop_audit_actor()
    await neo4j_client.close()
    logger.info("server_shutdown_complete")


def create_app() -> FastAPI:
    """FastAPI Application factory."""
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="FastAPI Backend for DICOM Knowledge Graph with Neo4j, PyDicom, PyNetDicom, Pykka, and MCP",
        lifespan=lifespan,
    )

    # Structured Request Tracing Middleware
    @app.middleware("http")
    async def structlog_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        bind_log_context(
            request_id=request_id,
            http_method=request.method,
            path=request.url.path,
            client_ip=request.client.host if request.client else "unknown",
        )
        start_time = time.perf_counter()
        logger.info("http_request_received")

        try:
            response: Response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info("http_request_finished", status_code=response.status_code, duration_ms=duration_ms)
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error("http_request_failed", error=str(exc), duration_ms=duration_ms, exc_info=True)
            raise
        finally:
            clear_log_context()

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routes
    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)
    from api.v1.endpoints.dicomweb import router as dicomweb_router

    app.include_router(dicomweb_router)

    # Mount Model Context Protocol (MCP) SSE Service
    from service.mcp_service import get_mcp_sse_app

    mcp_sse_app = get_mcp_sse_app()
    app.mount("/mcp", mcp_sse_app)

    # Allow direct root-level access for /sse and /messages (e.g. Cursor & MCP clients)
    class _MCPRootProxyMiddleware:
        def __init__(self, inner_app: Any, mcp_app: Any) -> None:
            self.inner_app = inner_app
            self.mcp_app = mcp_app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                path = scope.get("path", "").rstrip("/")
                if path in ("/sse", "/messages"):
                    await self.mcp_app(scope, receive, send)
                    return
            await self.inner_app(scope, receive, send)

    app.add_middleware(_MCPRootProxyMiddleware, mcp_app=mcp_sse_app)

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs_url": "/docs",
            "api_v1": settings.API_V1_PREFIX,
            "storage_root": settings.STORAGE_ROOT_URI,
            "db_connection": settings.DB_CONNECTION_URI,
        }

    return app


app = create_app()


def run() -> None:
    """Entrypoint to run the server directly."""
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    run()
