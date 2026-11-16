"""Integration tests for MCP RBAC and application lifespan."""

import uuid

import pytest

from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.mcp_service import _current_mcp_user, get_current_mcp_acted_by, update_system_info


@pytest.mark.anyio
async def test_mcp_context_propagation_and_rbac():
    test_id = f"test_mcp_ctx_{uuid.uuid4().hex[:8]}"

    # Clean pre-existing test data before running test
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    # Bootstrap default system node first
    from gosmart_graphite.service.system_service import system_service

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
