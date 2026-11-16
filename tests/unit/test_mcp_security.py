from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest

from gosmart_graphite.core.security import create_access_token
from gosmart_graphite.service.mcp_service import (
    get_mcp_sse_app,
    get_mcp_streamable_http_app,
    mcp,
)
from gosmart_graphite.service.user_service import user_service


@pytest.fixture
def offline_mcp_auth(monkeypatch: pytest.MonkeyPatch):
    """Keep MCP auth unit tests fully offline: the middleware resolves users via its JWT-claims
    fallback instead of querying the external Neo4j-backed user service."""
    monkeypatch.setattr(user_service, "get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(user_service, "authenticate_api_key", AsyncMock(return_value=None))


@pytest.fixture
def mcp_app():
    return get_mcp_sse_app()


@pytest.mark.anyio
async def test_mcp_sse_unauthenticated_rejected(mcp_app, offline_mcp_auth):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://testserver") as client:
        # GET /sse without auth header or query param
        res = await client.get("/sse")
        assert res.status_code == 401
        assert "Unauthorized" in res.json().get("detail", "")

        # POST /messages without auth
        res_post = await client.post("/messages")
        assert res_post.status_code == 401


@pytest.mark.anyio
async def test_mcp_sse_invalid_token_rejected(mcp_app, offline_mcp_auth):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://testserver") as client:
        # GET /sse with bad Bearer token
        res = await client.get("/sse", headers={"Authorization": "Bearer invalid.jwt.token"})
        assert res.status_code == 401

        # GET /sse with bad query token
        res_q = await client.get("/sse?token=invalid.jwt.token")
        assert res_q.status_code == 401

        # GET /sse with bad API key
        res_key = await client.get("/sse", headers={"X-API-Key": "gs_live_nonexistent_key"})
        assert res_key.status_code == 401


@pytest.mark.anyio
async def test_mcp_streamable_http_auth_and_handshake(offline_mcp_auth):
    """Verify StreamableHTTP transport authentication and initialize handshake."""
    streamable_app = get_mcp_streamable_http_app(streamable_http_path="/")

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }

    async with mcp.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=streamable_app), base_url="http://127.0.0.1:8086"
        ) as client:
            # 1. Unauthenticated request rejected with 401
            res_unauth = await client.post("/", json=init_payload)
            assert res_unauth.status_code == 401
            assert "Unauthorized" in res_unauth.json().get("detail", "")

            # 2. Authenticated with JWT Bearer Token
            valid_token = create_access_token(
                data={
                    "sub": "stream_user_1",
                    "email": "stream@gosmart.health",
                    "display_name": "Stream User",
                    "roles": ["PLATFORM_ADMIN"],
                }
            )
            res_auth = await client.post(
                "/",
                json=init_payload,
                headers={
                    "Authorization": f"Bearer {valid_token}",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert res_auth.status_code == 200
            assert "mcp-session-id" in res_auth.headers
            assert "application/json" in res_auth.headers.get(
                "content-type", ""
            ) or "text/event-stream" in res_auth.headers.get("content-type", "")
            assert "gosmart-graphite" in res_auth.text


@pytest.mark.anyio
async def test_mcp_streamable_http_via_main_app_lifespan(offline_mcp_auth):
    """Verify StreamableHTTP transport accessed via FastAPI root /mcp with lifespan."""
    from gosmart_graphite.main import create_app

    @asynccontextmanager
    async def isolated_lifespan(test_app):
        async with mcp.session_manager.run():
            yield

    test_app = create_app()
    test_app.router.lifespan_context = isolated_lifespan

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cline-test", "version": "1.0.0"},
        },
    }

    valid_token = create_access_token(
        data={
            "sub": "cline_user_1",
            "email": "cline@gosmart.health",
            "display_name": "Cline Agent",
            "roles": ["PLATFORM_ADMIN"],
        }
    )

    async with test_app.router.lifespan_context(test_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=test_app), base_url="http://127.0.0.1:8086"
        ) as client:
            # 1. Unauthenticated request rejected with 401
            res_unauth = await client.post("/mcp", json=init_payload)
            assert res_unauth.status_code == 401
            assert "Unauthorized" in res_unauth.json().get("detail", "")

            # 2. Authenticated initialize request on /mcp with token param
            res_init = await client.post(
                f"/mcp?token={valid_token}",
                json=init_payload,
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert res_init.status_code == 200
            session_id = res_init.headers.get("mcp-session-id")
            assert session_id is not None
            assert "gosmart-graphite" in res_init.text

            # 3. Subsequent request with mcp-session-id header (without token in query or header)
            res_subsequent = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
                headers={
                    "mcp-session-id": session_id,
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert res_subsequent.status_code == 200
            assert "generate_mock_dicom" in res_subsequent.text
