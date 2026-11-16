from unittest.mock import AsyncMock, patch

import pytest

from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.mcp_service import mcp


@pytest.mark.anyio
async def test_mcp_list_tools():
    tools = await mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "health_check" in tool_names
    assert "generate_mock_dicom" in tool_names
    assert "ingest_dicom_file" in tool_names
    assert "list_patients" in tool_names
    assert "get_patient_graph" in tool_names
    assert "execute_cypher" in tool_names
    assert "echo_scu" in tool_names


@pytest.mark.anyio
async def test_mcp_generate_mock_tool():
    # Mock the Neo4j hierarchy write so the unit test never reaches the external graph database.
    with patch.object(
        graph_service,
        "save_hierarchy",
        new=AsyncMock(return_value={"saved": True, "mock": True}),
    ) as mock_save:
        res = await mcp.call_tool(
            "generate_mock_dicom",
            {"patient_id": "MCP-TEST-01", "modality": "MR"},
        )
        assert res is not None
        assert not res.is_error
        assert len(res.content) >= 1
        mock_save.assert_awaited_once()
