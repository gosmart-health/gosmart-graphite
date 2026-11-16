"""Integration tests for group-scoped LLM configuration in Neo4j."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.service.embedding_service import EmbeddingService
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.group_service import group_service
from gosmart_graphite.service.llm_config_service import llm_config_service

# ---------------------------------------------------------------------------
# 6. Live Neo4j Integration Test (Pre-Test Cleanup, Preserves Post-Test State)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_live_neo4j_hierarchy_resolution_and_overwrite() -> None:
    """End-to-end Neo4j test for group-hierarchy LLM_Config resolution and overwrite archiving.

    Skips gracefully when Neo4j is unreachable. Per AGENTS.md, uses test_id isolation with
    pre-test cleanup only (no post-test deletion).
    """
    try:
        ok = await neo4j_client.verify_connectivity()
    except Exception:
        ok = False
    if not ok:
        await neo4j_client.close()
        pytest.skip("Neo4j database is unreachable; skipping live LLM_Config Neo4j integration test.")

    test_id = f"test_llm_{uuid.uuid4().hex[:8]}"
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        root_grp = await group_service.create_group(
            name="GoSmart Health",
            code=f"GSH_{uuid.uuid4().hex[:4]}",
            group_type="HEALTH_SYSTEM",
            test_id=test_id,
        )
        parent_grp = await group_service.create_group(
            name="Test Regional Hospital",
            code=f"TRH_{uuid.uuid4().hex[:4]}",
            group_type="HOSPITAL",
            test_id=test_id,
        )
        child_grp = await group_service.create_group(
            name="Test Outpatient Clinic",
            code=f"TOC_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )
        await group_service.nest_group(child_grp["id"], parent_grp["id"], test_id=test_id)
        await group_service.nest_group(parent_grp["id"], root_grp["id"], test_id=test_id)

        # Register default 'embedding' config on root group
        root_res = await llm_config_service.upsert_llm_config(
            config_data={
                "tag": "embedding",
                "provider": "ollama",
                "model": "nomic-embed-text:latest",
                "url": "http://localhost:11434",
                "dimensions": 4,
            },
            assign_group=root_grp["id"],
            acted_by="pytest",
            test_id=test_id,
        )
        assert root_res["tag"] == "embedding"

        # Child inherits root's config via SUB_GROUP_OF*0..
        resolved_for_child = await llm_config_service.resolve_config_for_group(
            tag="embedding",
            group_id=child_grp["id"],
            test_id=test_id,
        )
        assert resolved_for_child is not None
        assert resolved_for_child.model == "nomic-embed-text:latest"

        # Overwrite config and verify EmbeddingService executes dynamically with mock Genkit
        await llm_config_service.upsert_llm_config(
            config_data={
                "tag": "embedding",
                "provider": "vertexai",
                "model": "gemini-embedding-001",
                "dimensions": 4,
                "dimensions_truncated": 2,
                "project_id": "test-proj",
            },
            assign_group=root_grp["id"],
            acted_by="pytest",
            test_id=test_id,
        )

        resolved_after = await llm_config_service.resolve_config_for_group(
            tag="embedding",
            group_id=child_grp["id"],
            test_id=test_id,
        )
        assert resolved_after is not None
        assert resolved_after.provider == "vertexai"

        mock_ai = MagicMock()
        mock_ai.embed = AsyncMock(return_value=[SimpleNamespace(embedding=[3.0, 4.0, 9.0, 9.0])])
        emb_svc = EmbeddingService(ai=mock_ai)
        vec = await emb_svc.embed_text_async("Dyspnea evaluation", group_id=child_grp["id"], test_id=test_id)
        assert len(vec) == 2
    finally:
        await neo4j_client.close()


@pytest.mark.anyio
async def test_live_neo4j_same_tag_across_multiple_sponsor_groups() -> None:
    """Verify multiple sponsor groups can hold independent configs for the same tag (e.g. different API keys)."""
    try:
        ok = await neo4j_client.verify_connectivity()
    except Exception:
        ok = False
    if not ok:
        await neo4j_client.close()
        pytest.skip("Neo4j database is unreachable; skipping live multi-group same-tag Neo4j test.")

    test_id = f"test_llm_multi_{uuid.uuid4().hex[:8]}"
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        root_grp = await group_service.create_group(
            name="GoSmart Health",
            code=f"GSH_{uuid.uuid4().hex[:4]}",
            group_type="HEALTH_SYSTEM",
            test_id=test_id,
        )
        sponsor_a = await group_service.create_group(
            name="Sponsor A",
            code=f"SPA_{uuid.uuid4().hex[:4]}",
            group_type="HOSPITAL",
            test_id=test_id,
        )
        sponsor_b = await group_service.create_group(
            name="Sponsor B",
            code=f"SPB_{uuid.uuid4().hex[:4]}",
            group_type="HOSPITAL",
            test_id=test_id,
        )
        clinic_a = await group_service.create_group(
            name="Clinic A",
            code=f"CLA_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )
        clinic_b = await group_service.create_group(
            name="Clinic B",
            code=f"CLB_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )
        await group_service.nest_group(sponsor_a["id"], root_grp["id"], test_id=test_id)
        await group_service.nest_group(sponsor_b["id"], root_grp["id"], test_id=test_id)
        await group_service.nest_group(clinic_a["id"], sponsor_a["id"], test_id=test_id)
        await group_service.nest_group(clinic_b["id"], sponsor_b["id"], test_id=test_id)

        res_root = await llm_config_service.upsert_llm_config(
            config_data={
                "tag": "embedding",
                "provider": "vertexai",
                "model": "gemini-embedding-001",
                "api_key": "AIzaSyRootSponsorKey0000",
                "dimensions": 768,
            },
            assign_group=root_grp["id"],
            acted_by="pytest",
            test_id=test_id,
        )
        res_a = await llm_config_service.upsert_llm_config(
            config_data={
                "tag": "embedding",
                "provider": "vertexai",
                "model": "gemini-embedding-001",
                "api_key": "AIzaSySponsorAKey1111",
                "dimensions": 768,
            },
            assign_group=sponsor_a["id"],
            acted_by="pytest",
            test_id=test_id,
        )
        res_b = await llm_config_service.upsert_llm_config(
            config_data={
                "tag": "embedding",
                "provider": "vertexai",
                "model": "gemini-embedding-001",
                "api_key": "AIzaSySponsorBKey2222",
                "dimensions": 768,
            },
            assign_group=sponsor_b["id"],
            acted_by="pytest",
            test_id=test_id,
        )

        assert len({res_root["id"], res_a["id"], res_b["id"]}) == 3
        listed = await llm_config_service.list_llm_configs(tag="embedding", test_id=test_id)
        assert len(listed) == 3

        cfg_clinic_a = await llm_config_service.resolve_config_for_group(
            tag="embedding", group_id=clinic_a["id"], test_id=test_id
        )
        cfg_clinic_b = await llm_config_service.resolve_config_for_group(
            tag="embedding", group_id=clinic_b["id"], test_id=test_id
        )
        assert cfg_clinic_a is not None
        assert cfg_clinic_b is not None
        assert cfg_clinic_a.resolve_api_key() == "AIzaSySponsorAKey1111"
        assert cfg_clinic_b.resolve_api_key() == "AIzaSySponsorBKey2222"

        fetched_a = await llm_config_service.get_llm_config(tag="embedding", group_id=sponsor_a["id"], test_id=test_id)
        assert fetched_a is not None
        assert fetched_a["id"] == res_a["id"]

        with pytest.raises(RuntimeError, match="Ambiguous LLM_Config"):
            await llm_config_service.get_llm_config(tag="embedding", test_id=test_id)

        # Duplicate tag assignment onto sponsor_b must be rejected
        with pytest.raises(ValueError, match="already has an active LLM_Config with tag 'embedding'"):
            await llm_config_service.assign_to_group(
                config_id=res_a["id"],
                group_identifier=sponsor_b["id"],
                acted_by="pytest",
                test_id=test_id,
            )

        # Overwriting sponsor_b's config keeps sponsor_a's config untouched
        await llm_config_service.upsert_llm_config(
            config_data={
                "tag": "embedding",
                "provider": "vertexai",
                "model": "gemini-embedding-001",
                "api_key": "AIzaSySponsorBKey9999",
                "dimensions": 768,
            },
            assign_group=sponsor_b["id"],
            acted_by="pytest",
            test_id=test_id,
        )
        cfg_clinic_a_after = await llm_config_service.resolve_config_for_group(
            tag="embedding", group_id=clinic_a["id"], test_id=test_id
        )
        cfg_clinic_b_after = await llm_config_service.resolve_config_for_group(
            tag="embedding", group_id=clinic_b["id"], test_id=test_id
        )
        assert cfg_clinic_a_after is not None and cfg_clinic_a_after.resolve_api_key() == "AIzaSySponsorAKey1111"
        assert cfg_clinic_b_after is not None and cfg_clinic_b_after.resolve_api_key() == "AIzaSySponsorBKey9999"
    finally:
        await neo4j_client.close()
