import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from actor.dicom_actor import get_dicom_actor, stop_dicom_actor
from api.v1.router import api_v1_router
from core.config import get_settings
from core.logging import bind_log_context, clear_log_context, get_logger, setup_logging
from db.neo4j import neo4j_client

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
        await neo4j_client.init_schema_constraints()
    except Exception as e:
        logger.warning("neo4j_init_notice", error=str(e))

    # Initialize Pykka background actor
    actor_ref = get_dicom_actor()
    logger.info("pykka_actor_started", actor_urn=actor_ref.actor_urn)

    yield

    # Clean shutdown
    logger.info("server_shutting_down", app_name=settings.APP_NAME)
    stop_dicom_actor()
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
