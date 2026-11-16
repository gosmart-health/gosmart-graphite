"""Live MCP SSE authentication tests requiring a running server and Neo4j."""

import socket
import uuid

import httpx
import pytest

from gosmart_graphite.core.security import create_access_token
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.group_service import group_service
from gosmart_graphite.service.user_service import user_service

LOCAL_MCP_HOST = "127.0.0.1"
LOCAL_MCP_PORT = 8086
LOCAL_MCP_URL = f"http://{LOCAL_MCP_HOST}:{LOCAL_MCP_PORT}"


def is_server_listening(host: str = LOCAL_MCP_HOST, port: int = LOCAL_MCP_PORT, timeout: float = 0.5) -> bool:
    """Check if a TCP server is actively listening on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError, ConnectionRefusedError:
        return False


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
