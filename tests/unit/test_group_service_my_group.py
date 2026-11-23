"""Unit tests for GroupService.my_group, my_group_sync, and group_or_lost_and_found."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gosmart_graphite.model import Group, GroupNode, HasLostAndFoundRelation
from gosmart_graphite.model.fhir.relations import FHIR_RELATION_CLASSES
from gosmart_graphite.service import (
    DEFAULT_MY_GROUP_MAX_DEPTH,
    LOST_AND_FOUND_GROUP_CODE,
    LOST_AND_FOUND_GROUP_NAME,
    LOST_AND_FOUND_GROUP_TYPE,
    UPWARD_GROUP_RELATION_TYPES,
    GraphService,
    GroupService,
    group_or_lost_and_found,
    my_group,
    my_group_sync,
)
from gosmart_graphite.service.group_service import _build_my_group_query


def test_group_alias_and_curated_upward_relation_types() -> None:
    assert Group is GroupNode
    assert DEFAULT_MY_GROUP_MAX_DEPTH == 8
    assert HasLostAndFoundRelation.__type__ == "HAS_LOST_AND_FOUND"

    expected_core_rels = {
        "MEMBER_OF",
        "HAS_IDENTITY",
        "USES_AUTHENTICATOR",
        "HAS_AUTHENTICATOR",
        "BELONGS_TO",
        "PERFORMED_AT",
        "SERVES",
        "SCOPED_TO",
        "HAS_DICOM_CONFIGURATION",
        "HAS_LLM_CONFIG",
        "HAS_FHIR_CONFIG",
        "HAS_STUDY",
        "HAS_SERIES",
        "HAS_INSTANCE",
    }
    assert expected_core_rels.issubset(set(UPWARD_GROUP_RELATION_TYPES))

    for fhir_rel_cls in FHIR_RELATION_CLASSES:
        assert fhir_rel_cls.__type__ in UPWARD_GROUP_RELATION_TYPES

    # Ensure no duplicates and no LostAndFound or deprecated SUB_GROUP_OF relation
    assert len(UPWARD_GROUP_RELATION_TYPES) == len(set(UPWARD_GROUP_RELATION_TYPES))
    assert "SUB_GROUP_OF" not in UPWARD_GROUP_RELATION_TYPES
    assert "HAS_LOST_AND_FOUND" not in UPWARD_GROUP_RELATION_TYPES


def test_build_my_group_query_depth_and_guards() -> None:
    q_default = _build_my_group_query()
    assert "*0..8]-(g:Group)" in q_default
    assert "NOT 'System' IN labels(n)" in q_default
    assert "NOT type(r) STARTS WITH 'NOT_'" in q_default
    assert "ORDER BY depth ASC" in q_default
    assert "LIMIT 1" in q_default

    q_custom = _build_my_group_query(max_depth=4)
    assert "*0..4]-(g:Group)" in q_custom

    q_zero = _build_my_group_query(max_depth=0)
    assert "*0..0]-(g:Group)" in q_zero

    with pytest.raises(ValueError, match="non-negative integer"):
        _build_my_group_query(max_depth=-1)

    invalid_bool_depth: Any = True
    with pytest.raises(ValueError, match="non-negative integer"):
        _build_my_group_query(max_depth=invalid_bool_depth)


@pytest.mark.anyio
async def test_my_group_async_returns_group_node_and_handles_empty() -> None:
    mock_client = MagicMock()
    svc = GroupService(client=mock_client)

    # 1. Blank/whitespace node_id returns None without querying Neo4j
    mock_client.execute_query = AsyncMock()
    assert await svc.my_group("   ") is None
    mock_client.execute_query.assert_not_called()

    # 2. Negative max_depth raises ValueError
    with pytest.raises(ValueError, match="non-negative integer"):
        await svc.my_group("node_123", max_depth=-2)

    # 3. Unreachable / unknown node returns LostAndFound by default and logs decision
    mock_client.execute_query = AsyncMock(return_value=[])
    with patch("gosmart_graphite.service.group_service.logger") as mock_logger:
        orphan_result = await svc.my_group("orphan_node_id")
    assert orphan_result == svc.lost_and_found_group
    mock_client.execute_query.assert_awaited_once()
    mock_logger.info.assert_called_once()
    assert mock_logger.info.call_args.args[0] == "my_group_not_found_returning_lost_and_found"
    assert mock_logger.info.call_args.kwargs["decision"] == "return_lost_and_found_group"
    assert mock_logger.info.call_args.kwargs["return_lost_and_found_group"] is True

    # 3b. Unreachable / unknown node returns None when return_lost_and_found_group=False and logs decision
    mock_client.execute_query = AsyncMock(return_value=[])
    with patch("gosmart_graphite.service.group_service.logger") as mock_logger:
        assert await svc.my_group("orphan_node_id", return_lost_and_found_group=False) is None
    mock_client.execute_query.assert_awaited_once()
    mock_logger.info.assert_called_once()
    assert mock_logger.info.call_args.args[0] == "my_group_not_found_returning_none"
    assert mock_logger.info.call_args.kwargs["decision"] == "return_none"
    assert mock_logger.info.call_args.kwargs["return_lost_and_found_group"] is False

    # 4. Nearest Group found (e.g. Instance -> Series -> Study -> Group or 0-hop Group)
    expected_grp = GroupNode(
        name="Half Moon Bay Clinic",
        code="HMB_IMG_01",
        group_type="FACILITY",
        is_active=True,
        description="Coastal imaging facility",
    )
    mock_client.execute_query = AsyncMock(
        return_value=[
            {
                "group": {
                    "id": expected_grp.id,
                    "name": "Half Moon Bay Clinic",
                    "code": "HMB_IMG_01",
                    "group_type": "FACILITY",
                    "is_active": True,
                    "description": "Coastal imaging facility",
                    "Action": "CREATE",
                },
                "depth": 3,
            }
        ]
    )

    result = await svc.my_group("  inst_node_999  ", max_depth=6)
    assert isinstance(result, GroupNode)
    assert result.id == expected_grp.id
    assert result.name == "Half Moon Bay Clinic"
    assert result.code == "HMB_IMG_01"
    assert result.group_type == "FACILITY"

    called_query, called_params = mock_client.execute_query.call_args.args
    assert "*0..6]-(g:Group)" in called_query
    assert called_params == {"node_id": "inst_node_999"}


def test_my_group_sync_returns_group_node_and_handles_errors() -> None:
    mock_client = MagicMock()
    mock_client.settings.DB_DATABASE = "neo4j"
    svc = GroupService(client=mock_client)

    # 1. Empty node_id returns None without touching driver
    assert svc.my_group_sync("") is None
    mock_client.get_sync_driver.assert_not_called()

    # 2. Invalid max_depth raises ValueError
    with pytest.raises(ValueError, match="non-negative integer"):
        svc.my_group_sync("node_123", max_depth=-1)

    # 3. Valid sync query returns GroupNode
    expected_grp = GroupNode(name="GoSmart Health", code="GOSMART_HEALTH", group_type="HEALTH_SYSTEM")
    mock_record = MagicMock()
    mock_record.data.return_value = {
        "group": {
            "id": expected_grp.id,
            "name": "GoSmart Health",
            "code": "GOSMART_HEALTH",
            "group_type": "HEALTH_SYSTEM",
            "is_active": True,
        },
        "depth": 1,
    }
    mock_result = MagicMock()
    mock_result.single.return_value = mock_record

    mock_session = MagicMock()
    mock_session.run.return_value = mock_result
    mock_session_cm = MagicMock()
    mock_session_cm.__enter__.return_value = mock_session
    mock_session_cm.__exit__.return_value = False

    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session_cm
    mock_client.get_sync_driver.return_value = mock_driver

    grp = svc.my_group_sync("fhir_cfg_001")
    assert isinstance(grp, GroupNode)
    assert grp.id == expected_grp.id
    assert grp.code == "GOSMART_HEALTH"
    mock_session.run.assert_called_once()
    run_query = mock_session.run.call_args.args[0]
    assert "*0..8]-(g:Group)" in run_query
    assert mock_session.run.call_args.kwargs == {"node_id": "fhir_cfg_001"}

    # 3b. Empty sync result respects return_lost_and_found_group flag and logs decision
    mock_result.single.return_value = None
    with patch("gosmart_graphite.service.group_service.logger") as mock_logger:
        assert svc.my_group_sync("orphan_sync") == svc.lost_and_found_group
    mock_logger.info.assert_called_once()
    assert mock_logger.info.call_args.args[0] == "my_group_not_found_returning_lost_and_found"
    assert mock_logger.info.call_args.kwargs["method"] == "my_group_sync"
    assert mock_logger.info.call_args.kwargs["decision"] == "return_lost_and_found_group"

    with patch("gosmart_graphite.service.group_service.logger") as mock_logger:
        assert svc.my_group_sync("orphan_sync", return_lost_and_found_group=False) is None
    mock_logger.info.assert_called_once()
    assert mock_logger.info.call_args.args[0] == "my_group_not_found_returning_none"
    assert mock_logger.info.call_args.kwargs["method"] == "my_group_sync"
    assert mock_logger.info.call_args.kwargs["decision"] == "return_none"

    # 4. Driver exception logs warning and applies return_lost_and_found_group decision
    mock_client.get_sync_driver.side_effect = RuntimeError("Driver closed")
    with patch("gosmart_graphite.service.group_service.logger") as mock_logger:
        assert svc.my_group_sync("fhir_cfg_001") == svc.lost_and_found_group
    mock_logger.warning.assert_called_once()
    mock_logger.info.assert_called_once()
    assert mock_logger.info.call_args.kwargs["decision"] == "return_lost_and_found_group"

    with patch("gosmart_graphite.service.group_service.logger") as mock_logger:
        assert svc.my_group_sync("fhir_cfg_001", return_lost_and_found_group=False) is None
    mock_logger.warning.assert_called_once()
    mock_logger.info.assert_called_once()
    assert mock_logger.info.call_args.kwargs["decision"] == "return_none"


@pytest.mark.anyio
async def test_top_level_my_group_and_my_group_sync_helpers() -> None:
    expected = GroupNode(name="GoSmart Health", code="GOSMART_HEALTH", group_type="HEALTH_SYSTEM")

    with patch.object(GroupService, "my_group", new=AsyncMock(return_value=expected)) as mock_async:
        res_async = await my_group("node_abc")
        assert res_async == expected
        mock_async.assert_awaited_once_with(node_id="node_abc", max_depth=8, return_lost_and_found_group=True)

    with patch.object(GroupService, "my_group", new=AsyncMock(return_value=None)) as mock_async:
        res_async_none = await my_group("node_abc", return_lost_and_found_group=False)
        assert res_async_none is None
        mock_async.assert_awaited_once_with(node_id="node_abc", max_depth=8, return_lost_and_found_group=False)

    with patch.object(GroupService, "my_group_sync", return_value=expected) as mock_sync:
        res_sync = my_group_sync("node_abc", max_depth=5)
        assert res_sync == expected
        mock_sync.assert_called_once_with(node_id="node_abc", max_depth=5, return_lost_and_found_group=True)

    with patch.object(GroupService, "my_group_sync", return_value=None) as mock_sync:
        res_sync_none = my_group_sync("node_abc", max_depth=5, return_lost_and_found_group=False)
        assert res_sync_none is None
        mock_sync.assert_called_once_with(node_id="node_abc", max_depth=5, return_lost_and_found_group=False)


@pytest.mark.anyio
async def test_group_or_lost_and_found_fallback_and_passthrough() -> None:
    mock_client = MagicMock()
    svc = GroupService(client=mock_client)

    # 1. None returns initialized LostAndFound GroupNode
    fallback = svc.group_or_lost_and_found(None)
    assert isinstance(fallback, GroupNode)
    assert fallback.name == LOST_AND_FOUND_GROUP_NAME == "LostAndFound"
    assert fallback.code == LOST_AND_FOUND_GROUP_CODE == "LOST_AND_FOUND"
    assert fallback.group_type == LOST_AND_FOUND_GROUP_TYPE == "SYSTEM"
    assert fallback.is_active is True
    assert fallback.id

    # 2. Existing GroupNode is returned unchanged
    clinic_grp = GroupNode(name="Half Moon Bay Clinic", code="HMB_IMG_01", group_type="FACILITY")
    assert svc.group_or_lost_and_found(clinic_grp) is clinic_grp

    # 3. Dict representation of a Group is converted to GroupNode
    dict_grp = svc.group_or_lost_and_found({"name": "Palo Alto Clinic", "code": "PA_CLINIC", "group_type": "FACILITY"})
    assert isinstance(dict_grp, GroupNode)
    assert dict_grp.code == "PA_CLINIC"

    # 4. Composing my_group(orphan) -> None with group_or_lost_and_found returns LostAndFound
    mock_client.execute_query = AsyncMock(return_value=[])
    resolved = svc.group_or_lost_and_found(await svc.my_group("dangling_study_node", return_lost_and_found_group=False))
    assert resolved.code == "LOST_AND_FOUND"
    assert resolved.name == "LostAndFound"

    # 5. Top-level helper delegates to group_service
    top_fallback = group_or_lost_and_found(None)
    assert isinstance(top_fallback, GroupNode)
    assert top_fallback.code == "LOST_AND_FOUND"


@pytest.mark.anyio
async def test_ensure_lost_and_found_group_async_and_sync_and_schema_init() -> None:
    mock_client = MagicMock()
    mock_client.settings.DB_DATABASE = "neo4j"
    svc = GroupService(client=mock_client)

    # 1. Async ensure_lost_and_found_group persists System -> HAS_LOST_AND_FOUND -> Group
    mock_client.execute_query = AsyncMock(
        return_value=[
            {
                "group": {
                    "id": svc.lost_and_found_group.id,
                    "name": "LostAndFound",
                    "code": "LOST_AND_FOUND",
                    "group_type": "SYSTEM",
                    "is_active": True,
                    "description": "Operational recovery group",
                }
            }
        ]
    )
    ensured_async = await svc.ensure_lost_and_found_group()
    assert isinstance(ensured_async, GroupNode)
    assert ensured_async.code == "LOST_AND_FOUND"
    assert ensured_async.name == "LostAndFound"
    mock_client.execute_query.assert_awaited_once()
    cypher_query, cypher_params = mock_client.execute_query.call_args.args
    assert "MERGE (s:System {id: $sys_id})" in cypher_query
    assert "MERGE (g:Group {code: $code})" in cypher_query
    assert "MERGE (s)-[r:HAS_LOST_AND_FOUND {id: $rel_id}]->(g)" in cypher_query
    assert cypher_params["code"] == "LOST_AND_FOUND"
    assert cypher_params["name"] == "LostAndFound"

    # 2. Sync ensure_lost_and_found_group_sync persists via sync session
    mock_record = MagicMock()
    mock_record.data.return_value = {
        "group": {
            "id": svc.lost_and_found_group.id,
            "name": "LostAndFound",
            "code": "LOST_AND_FOUND",
            "group_type": "SYSTEM",
            "is_active": True,
        }
    }
    mock_result = MagicMock()
    mock_result.single.return_value = mock_record
    mock_session = MagicMock()
    mock_session.run.return_value = mock_result
    mock_session_cm = MagicMock()
    mock_session_cm.__enter__.return_value = mock_session
    mock_session_cm.__exit__.return_value = False
    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session_cm
    mock_client.get_sync_driver.return_value = mock_driver

    ensured_sync = svc.ensure_lost_and_found_group_sync()
    assert ensured_sync.code == "LOST_AND_FOUND"
    mock_session.run.assert_called_once()

    # 3. GraphService.init_schema_constraints initializes LostAndFound Group at schema init time
    mock_graph_client = MagicMock()
    mock_graph_client.verify_connectivity = AsyncMock(return_value=True)
    mock_graph_client.execute_query = AsyncMock(return_value=[])
    graph_svc = GraphService(client=mock_graph_client)

    with patch.object(
        GroupService,
        "ensure_lost_and_found_group",
        new=AsyncMock(return_value=svc.lost_and_found_group),
    ) as mock_ensure_lnf:
        await graph_svc.init_schema_constraints()
        mock_ensure_lnf.assert_awaited_once()


@pytest.mark.anyio
async def test_catch_all_group_belongs_to_system_and_nest_group_uses_belongs_to() -> None:
    mock_client = MagicMock()
    mock_client.settings.DB_DATABASE = "neo4j"
    svc = GroupService(client=mock_client)

    expected_grp = GroupNode(
        name="GoSmart Health",
        code="GOSMART_HEALTH",
        group_type="HEALTH_SYSTEM",
        description="Catch-all root organization for unassociated and lost-and-found studies",
    )

    # 1. Async get_or_create_catch_all_group persists (:Group {name: 'GoSmart Health'})-[:BELONGS_TO]->(:System)
    mock_client.execute_query = AsyncMock(return_value=[{"group": expected_grp.model_dump()}])
    res_async = await svc.get_or_create_catch_all_group(acted_by="system:bootstrap")
    assert res_async["name"] == "GoSmart Health"
    assert res_async["code"] == "GOSMART_HEALTH"
    cypher_query, cypher_params = mock_client.execute_query.call_args.args
    assert "MERGE (s:System {id: $sys_id})" in cypher_query
    assert "MERGE (g:Group {code: $code})" in cypher_query
    assert "MERGE (g)-[r:BELONGS_TO {id: $rel_id}]->(s)" in cypher_query
    assert cypher_params["name"] == "GoSmart Health"
    assert cypher_params["code"] == "GOSMART_HEALTH"

    # 2. Sync get_or_create_catch_all_group_sync uses the same Cypher
    mock_result = MagicMock()
    mock_result.single.return_value = {"group": expected_grp.model_dump()}
    mock_session = MagicMock()
    mock_session.run.return_value = mock_result
    mock_session_cm = MagicMock()
    mock_session_cm.__enter__.return_value = mock_session
    mock_session_cm.__exit__.return_value = False
    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session_cm
    mock_client.get_sync_driver.return_value = mock_driver

    res_sync = svc.get_or_create_catch_all_group_sync(acted_by="system:sync")
    assert res_sync["name"] == "GoSmart Health"
    assert "MERGE (g)-[r:BELONGS_TO {id: $rel_id}]->(s)" in mock_session.run.call_args.args[0]

    # 3. nest_group creates (child:Group)-[:BELONGS_TO]->(parent:Group)
    mock_client.execute_query = AsyncMock(
        return_value=[{"rel": {"id": "rel_1"}, "child_id": "grp_child", "parent_id": "grp_parent"}]
    )
    nest_res = await svc.nest_group("grp_child", "grp_parent", acted_by="cli:admin")
    assert nest_res["child_id"] == "grp_child"
    nest_cypher = mock_client.execute_query.call_args.args[0]
    assert "MERGE (child)-[r:BELONGS_TO {id: $rel_id}]->(parent)" in nest_cypher
    assert "SUB_GROUP_OF" not in nest_cypher

    # 4. Catch-all Cypher ON MATCH never overwrites existing Group.test_id
    assert "g.test_id = coalesce($test_id, g.test_id)" not in cypher_query

    # 5. my_group query stops at Group and never traverses :System even with (:Group)-[:BELONGS_TO]->(:System)
    my_group_query = _build_my_group_query()
    assert "BELONGS_TO" in my_group_query
    assert "NOT 'System' IN labels(n)" in my_group_query
