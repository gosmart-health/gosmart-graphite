import socket
import uuid

import httpx
import pytest

from core.security import create_access_token
from service.graph_service import graph_service
from service.group_service import group_service
from service.mcp_service import (
    _current_mcp_user,
    get_current_mcp_acted_by,
    get_mcp_sse_app,
    update_system_info,
)
from service.user_service import user_service

LOCAL_MCP_HOST = "127.0.0.1"
LOCAL_MCP_PORT = 8001
LOCAL_MCP_URL = f"http://{LOCAL_MCP_HOST}:{LOCAL_MCP_PORT}"


def is_server_listening(host: str = LOCAL_MCP_HOST, port: int = LOCAL_MCP_PORT, timeout: float = 0.5) -> bool:
    """Check if a TCP server is actively listening on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError, ConnectionRefusedError:
        return False


@pytest.fixture
def mcp_app():
    return get_mcp_sse_app()


@pytest.mark.anyio
async def test_mcp_sse_unauthenticated_rejected(mcp_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url="http://testserver") as client:
        # GET /sse without auth header or query param
        res = await client.get("/sse")
        assert res.status_code == 401
        assert "Unauthorized" in res.json().get("detail", "")

        # POST /messages without auth
        res_post = await client.post("/messages")
        assert res_post.status_code == 401


@pytest.mark.anyio
async def test_mcp_sse_invalid_token_rejected(mcp_app):
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
async def test_mcp_sse_valid_jwt_and_api_key_auth():
    """Verify authenticated SSE stream connections against a running local MCP server.

    If the local server is not running on 127.0.0.1:8001, gracefully skip to prevent
    the test harness from hanging on infinite SSE streams with in-memory ASGI transport.
    """
    if not is_server_listening(LOCAL_MCP_HOST, LOCAL_MCP_PORT):
        pytest.skip(
            f"Local MCP server is not running on {LOCAL_MCP_URL}. "
            "Start the server before running live SSE stream tests to prevent test harness hangs."
        )

    test_id = f"test_mcp_sec_{uuid.uuid4().hex[:8]}"

    # Clean pre-existing test data before running test (preserve post-test data for manual inspection)
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    group = await group_service.create_group(
        name="MCP Clinic",
        code=f"MCP_{uuid.uuid4().hex[:4]}",
        test_id=test_id,
    )

    user = await user_service.create_local_user(
        username=f"mcp_agent_{uuid.uuid4().hex[:6]}",
        email=f"mcp_{uuid.uuid4().hex[:6]}@clinic.org",
        password="SecurePassword123!",
        display_name="MCP Autonomous Agent",
        system_roles=["USER", "Admin"],
        group_id=group["id"],
        group_roles=["Admin"],
        test_id=test_id,
    )

    jwt_token = create_access_token(
        data={
            "sub": user["id"],
            "email": user["primary_email"],
            "roles": user["system_roles"],
            "display_name": user["display_name"],
        }
    )

    api_key_data = await user_service.create_api_key(
        user_id=user["id"],
        name="MCP Testing API Key",
        scopes=["mcp:full"],
        test_id=test_id,
    )
    raw_api_key = api_key_data["raw_api_key"]

    async with httpx.AsyncClient(base_url=LOCAL_MCP_URL, timeout=5.0) as client:
        # 1. Bearer JWT in Header
        async with client.stream("GET", "/sse", headers={"Authorization": f"Bearer {jwt_token}"}) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

        # 2. JWT in query string ?token=...
        async with client.stream("GET", f"/sse?token={jwt_token}") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

        # 3. API Key in X-API-Key Header
        async with client.stream("GET", "/sse", headers={"X-API-Key": raw_api_key}) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

        # 4. API Key in query parameter ?api_key=...
        async with client.stream("GET", f"/sse?api_key={raw_api_key}") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")


@pytest.mark.anyio
async def test_mcp_context_propagation_and_rbac():
    test_id = f"test_mcp_ctx_{uuid.uuid4().hex[:8]}"

    # Clean pre-existing test data before running test
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    # Bootstrap default system node first
    from service.system_service import system_service

    await system_service.bootstrap_system(test_id=test_id)

    # Default fallback when no user is set in context
    token_ctx = _current_mcp_user.set(None)
    assert get_current_mcp_acted_by(fallback="custom_fallback") == "custom_fallback"
    _current_mcp_user.reset(token_ctx)

    # Set standard (non-admin) user
    non_admin_user = {
        "id": f"usr_{uuid.uuid4().hex[:8]}",
        "primary_email": "nurse@clinic.org",
        "display_name": "Nurse Jane",
        "system_roles": ["USER"],
    }
    token_ctx = _current_mcp_user.set(non_admin_user)
    assert get_current_mcp_acted_by() == "user:nurse@clinic.org"

    # Attempting admin MCP tool as non-admin raises PermissionError
    with pytest.raises(PermissionError, match="Operation requires PLATFORM_ADMIN or Admin role"):
        await update_system_info(name="Unauthorized Name Change", test_id=test_id)

    _current_mcp_user.reset(token_ctx)

    # Set admin user
    admin_user = {
        "id": f"usr_{uuid.uuid4().hex[:8]}",
        "primary_email": "admin@clinic.org",
        "display_name": "Admin John",
        "system_roles": ["PLATFORM_ADMIN"],
    }
    token_ctx = _current_mcp_user.set(admin_user)
    assert get_current_mcp_acted_by() == "user:admin@clinic.org"

    # Calling admin tool succeeds
    updated = await update_system_info(name="Authorized Enterprise Name", test_id=test_id)
    assert updated is not None
    assert updated.get("name") == "Authorized Enterprise Name"

    _current_mcp_user.reset(token_ctx)
