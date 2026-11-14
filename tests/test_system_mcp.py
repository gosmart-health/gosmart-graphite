from unittest.mock import AsyncMock, patch

import pytest

from gosmart_graphite.service.mcp_service import (
    bootstrap_system_defaults,
    create_dicom_configuration,
    delete_dicom_configuration,
    get_dicom_configuration,
    get_system_info,
    list_dicom_configurations,
    restore_dicom_configuration,
    update_dicom_configuration,
    update_system_info,
)
from gosmart_graphite.service.system_service import system_service


@pytest.mark.anyio
async def test_mcp_system_tools():
    """Verify MCP administrative tools for System and Configuration management."""
    mock_system = {
        "id": "sys_mcp_test",
        "name": "GoSmart Health VNA",
        "owner": "GoSmart Health",
        "address": "Half Moon Bay, California",
        "email": "admin@gosmart.health",
        "phone": "+1 (650) 535-0097",
        "serial_number": "GSH-VNA-0001",
        "installed_at": "2026-09-30T00:00:00Z",
        "configurations_count": 1,
        "configurations": [],
    }

    mock_config = {
        "id": "cfg_mcp_test",
        "ae_title": "*",
        "roles": ["SCP"],
        "host": "0.0.0.0",
        "port": 11112,
        "is_promiscuous": True,
        "description": "Default MCP Promiscuous SCP",
    }

    # 1. bootstrap_system_defaults
    with patch.object(
        system_service,
        "bootstrap_system",
        new=AsyncMock(return_value={"success": True, "system": mock_system, "promiscuous_config": mock_config}),
    ):
        boot_res = await bootstrap_system_defaults(acted_by="mcp_test_agent")
        assert boot_res["success"] is True
        assert boot_res["system"]["name"] == "GoSmart Health VNA"

    # 2. get_system_info
    with patch.object(system_service, "get_system", new=AsyncMock(return_value=mock_system)):
        sys_info = await get_system_info()
        assert sys_info is not None
        assert sys_info["id"] == "sys_mcp_test"

    # 3. update_system_info
    updated_sys = dict(mock_system)
    updated_sys["owner"] = "Bay Area Hospital"
    with patch.object(system_service, "update_system", new=AsyncMock(return_value=updated_sys)):
        up_res = await update_system_info(owner="Bay Area Hospital", acted_by="mcp_test_agent")
        assert up_res is not None
        assert up_res["owner"] == "Bay Area Hospital"

    # 4. create_dicom_configuration
    with patch.object(system_service, "create_configuration", new=AsyncMock(return_value=mock_config)):
        created = await create_dicom_configuration(
            ae_title="*",
            roles_json='["SCP"]',
            host="0.0.0.0",
            port=11112,
            acted_by="mcp_test_agent",
        )
        assert created["ae_title"] == "*"
        assert created["is_promiscuous"] is True

    # 5. list_dicom_configurations
    with patch.object(system_service, "list_configurations", new=AsyncMock(return_value=[mock_config])):
        configs = await list_dicom_configurations()
        assert len(configs) == 1
        assert configs[0]["ae_title"] == "*"

    # 6. get_dicom_configuration
    with patch.object(system_service, "get_configuration", new=AsyncMock(return_value=mock_config)):
        fetched = await get_dicom_configuration(config_id="cfg_mcp_test")
        assert fetched is not None
        assert fetched["id"] == "cfg_mcp_test"

    # 7. update_dicom_configuration
    updated_cfg = dict(mock_config)
    updated_cfg["description"] = "Updated MCP Description"
    with patch.object(system_service, "update_configuration", new=AsyncMock(return_value=updated_cfg)):
        up_cfg = await update_dicom_configuration(
            config_id="cfg_mcp_test",
            description="Updated MCP Description",
            acted_by="mcp_test_agent",
        )
        assert up_cfg is not None
        assert up_cfg["description"] == "Updated MCP Description"

    # 8. delete_dicom_configuration
    with patch.object(system_service, "delete_configuration", new=AsyncMock(return_value=True)):
        del_res = await delete_dicom_configuration(config_id="cfg_mcp_test", acted_by="mcp_test_agent")
        assert del_res["success"] is True
        assert del_res["config_id"] == "cfg_mcp_test"

    # 9. restore_dicom_configuration
    with patch.object(system_service, "restore_configuration", new=AsyncMock(return_value=mock_config)):
        res_cfg = await restore_dicom_configuration(config_id="cfg_mcp_test", acted_by="mcp_test_agent")
        assert res_cfg["success"] is True
        assert res_cfg["configuration"]["id"] == "cfg_mcp_test"
