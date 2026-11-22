"""Integration tests for DiagnosticReportService against Neo4j and EmbeddingService."""

import base64

import numpy as np
import pytest
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.encounter import Encounter

from gosmart_graphite.actor.embedding_actor import EmbeddingActor
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model.actor_message import EmbeddingMessage, EmbeddingMethod
from gosmart_graphite.model.auth import Group, GroupNode
from gosmart_graphite.model.embedding import (
    DEFAULT_EMBEDDING_VECTOR_DIMENSIONS,
    EMBEDDING_VECTOR_INDEX_NAME,
    DiagnosticReportFieldTag,
)
from gosmart_graphite.model.llm_config import LLMConfigCreateRequest
from gosmart_graphite.model.result import STATUS_BAD_DATA, STATUS_OK
from gosmart_graphite.service.diagnostic_report_service import DiagnosticReportService, ReportTexts
from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG, EmbeddingService
from gosmart_graphite.service.fhir_service import EncRelevantResources, FhirService
from gosmart_graphite.service.graph_service import GraphService
from gosmart_graphite.service.llm_config_service import LLMConfigService


@pytest.mark.anyio
async def test_config_ensure_resolves_group_and_llm_config_from_neo4j():
    """Verify config_ensure resolves Group via DiagnosticReport->Encounter->Group and group LLM_Config."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    test_id = "test_diagnostic_report_config_ensure"
    fhir_svc = FhirService()
    llm_svc = LLMConfigService()
    dr_svc = DiagnosticReportService()

    # Clean up prior test-generated data BEFORE the test (retain after test for inspection)
    await fhir_svc.cleanup_upserted_resources(test_id=test_id)

    grp = GroupNode(
        name="DR Config Ensure Clinic",
        code="DR_CFG_ENSURE_CLINIC",
        group_type="FACILITY",
        is_active=True,
        test_id=test_id,
        ActedBy="pytest",
    )
    await neo4j_client.execute_query(
        """
        MERGE (g:Group {id: $id})
        SET g.name = $name,
            g.code = $code,
            g.group_type = $group_type,
            g.is_active = true,
            g.test_id = $test_id,
            g.Action = 'CREATE',
            g.ActedAt = datetime({timezone: 'UTC'}),
            g.ActedBy = 'pytest',
            g.CreatedAt = datetime({timezone: 'UTC'})
        """,
        {
            "id": grp.id,
            "name": grp.name,
            "code": grp.code,
            "group_type": grp.group_type,
            "test_id": test_id,
        },
    )

    await llm_svc.upsert_llm_config(
        LLMConfigCreateRequest(
            tag=DEFAULT_EMBEDDING_TAG,
            provider="ollama",
            model="nomic-embed-text:latest",
            url="http://localhost:11434",
            dimensions=768,
            assign_group=grp.id,
            test_id=test_id,
        ),
        acted_by="pytest",
    )

    enc_r4b = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-dr-cfg-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        }
    )
    dr_r4b = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "dr-cfg-1",
            "status": "final",
            "code": {"text": "CT CHEST"},
            "encounter": {"reference": "Encounter/enc-dr-cfg-1"},
            "conclusion": "Normal chest CT.",
        }
    )

    upsert_res = await fhir_svc.upsert_relevant_resources(
        EncRelevantResources(encounter=enc_r4b, diagnostic_reports=[dr_r4b]),
        test_id=test_id,
        acted_by="pytest",
    )
    enc_node_id = next(n["id"] for n in upsert_res["nodes"] if n["label"] == "Encounter")
    link_res = await fhir_svc.link_encounter_to_group(
        encounter_id=enc_node_id,
        group_id=grp.id,
        test_id=test_id,
        acted_by="pytest",
    )
    assert link_res["linked"] is True

    # 1. Resolve via persisted DiagnosticReport node -> Encounter -> Group -> LLM_Config
    report = DiagnosticReport(
        id="dr-cfg-1",
        status="final",
        code={"text": "CT CHEST"},
        encounter={"reference": "Encounter/enc-dr-cfg-1"},
    )
    rt_primary = ReportTexts(report=report)
    res_primary = dr_svc.config_ensure(rt_primary, test_id=test_id)
    assert res_primary.status == STATUS_OK
    assert rt_primary.group_context is not None
    assert rt_primary.group_context.id == grp.id
    assert rt_primary.llm_config is not None
    assert rt_primary.llm_config.tag == DEFAULT_EMBEDDING_TAG
    assert rt_primary.llm_config.model == "nomic-embed-text:latest"

    # 2. Unpersisted DiagnosticReport falls back to referenced Encounter node -> Group -> LLM_Config
    report_via_enc = DiagnosticReport(
        id="dr-cfg-unpersisted",
        status="final",
        code={"text": "CT CHEST"},
        encounter={"reference": "Encounter/enc-dr-cfg-1"},
    )
    rt_fallback_enc = ReportTexts(report=report_via_enc)
    res_enc = dr_svc.config_ensure(rt_fallback_enc, test_id=test_id)
    assert res_enc.status == STATUS_OK
    assert rt_fallback_enc.group_context is not None
    assert rt_fallback_enc.group_context.id == grp.id
    assert rt_fallback_enc.llm_config is not None
    assert rt_fallback_enc.llm_config.model == "nomic-embed-text:latest"

    # 3. Orphan report falls back to LostAndFound; without a test_id default LLM_Config returns STATUS_BAD_DATA
    orphan_report = DiagnosticReport(
        id="dr-cfg-orphan",
        status="final",
        code={"text": "CT CHEST"},
    )
    rt_orphan = ReportTexts(report=orphan_report)
    res_orphan = dr_svc.config_ensure(rt_orphan, test_id=test_id)
    assert rt_orphan.group_context is not None
    assert rt_orphan.group_context.code == "LOST_AND_FOUND"
    assert res_orphan.status == STATUS_BAD_DATA


@pytest.mark.anyio
async def test_compute_embeddings_against_neo4j_and_embedder():
    """Verify parse_diagnostic_report -> compute_embeddings end-to-end against Neo4j and active embedder."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    llm_svc = LLMConfigService()
    emb_svc = EmbeddingService()
    active_llm_cfg = await llm_svc.resolve_config_for_group(tag=DEFAULT_EMBEDDING_TAG)
    if active_llm_cfg is None or not emb_svc.is_configured(llm_config=active_llm_cfg):
        pytest.skip(f"No active usable '{DEFAULT_EMBEDDING_TAG}' LLM_Config configured in Neo4j; skipping live test.")

    try:
        await emb_svc.embed_text_async("Health check.", llm_config=active_llm_cfg)
    except Exception as exc:
        pytest.skip(f"Configured embedding provider is not reachable: {exc}")

    test_id = "test_diagnostic_report_compute_embeddings"
    fhir_svc = FhirService()
    dr_svc = DiagnosticReportService(llm_config_service=llm_svc, embedding_service=emb_svc)

    # Clean up prior test-generated data BEFORE the test (retain after test for inspection)
    await fhir_svc.cleanup_upserted_resources(test_id=test_id)

    grp = GroupNode(
        name="DR Embed Clinic",
        code="DR_EMBED_CLINIC",
        group_type="FACILITY",
        is_active=True,
        test_id=test_id,
        ActedBy="pytest",
    )
    await neo4j_client.execute_query(
        """
        MERGE (g:Group {id: $id})
        SET g.name = $name,
            g.code = $code,
            g.group_type = $group_type,
            g.is_active = true,
            g.test_id = $test_id,
            g.Action = 'CREATE',
            g.ActedAt = datetime({timezone: 'UTC'}),
            g.ActedBy = 'pytest',
            g.CreatedAt = datetime({timezone: 'UTC'})
        """,
        {
            "id": grp.id,
            "name": grp.name,
            "code": grp.code,
            "group_type": grp.group_type,
            "test_id": test_id,
        },
    )

    await llm_svc.upsert_llm_config(
        LLMConfigCreateRequest(
            tag=DEFAULT_EMBEDDING_TAG,
            provider=active_llm_cfg.provider,
            model=active_llm_cfg.model,
            url=active_llm_cfg.url,
            api_key=active_llm_cfg.api_key,
            dimensions=active_llm_cfg.dimensions,
            dimensions_truncated=active_llm_cfg.dimensions_truncated,
            project_id=active_llm_cfg.project_id,
            location=active_llm_cfg.location,
            extra_config=active_llm_cfg.extra_config,
            assign_group=grp.id,
            test_id=test_id,
        ),
        acted_by="pytest",
    )

    enc_r4b = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-dr-emb-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        }
    )
    dr_r4b = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "dr-emb-1",
            "status": "final",
            "code": {"text": "CT CHEST"},
            "encounter": {"reference": "Encounter/enc-dr-emb-1"},
            "conclusion": "No acute cardiopulmonary abnormality.",
        }
    )
    upsert_res = await fhir_svc.upsert_relevant_resources(
        EncRelevantResources(encounter=enc_r4b, diagnostic_reports=[dr_r4b]),
        test_id=test_id,
        acted_by="pytest",
    )
    enc_node_id = next(n["id"] for n in upsert_res["nodes"] if n["label"] == "Encounter")
    link_res = await fhir_svc.link_encounter_to_group(
        encounter_id=enc_node_id,
        group_id=grp.id,
        test_id=test_id,
        acted_by="pytest",
    )
    assert link_res["linked"] is True

    # 1. Both Findings and Conclusion present -> two non-empty L2-normalized vectors of matching length
    report_text = "Findings: The lungs are clear without consolidation or pleural effusion.\nImpression: Normal chest."
    report_both = DiagnosticReport(
        id="dr-emb-1",
        status="final",
        code={"text": "CT CHEST"},
        encounter={"reference": "Encounter/enc-dr-emb-1"},
        presentedForm=[
            {
                "contentType": "text/plain",
                "data": base64.b64encode(report_text.encode("utf-8")).decode("ascii"),
            }
        ],
    )
    rt_both = dr_svc.parse_diagnostic_report(report_both)
    res_both = await dr_svc.compute_embeddings(rt_both, test_id=test_id)
    assert res_both.status == STATUS_OK
    assert len(rt_both.embedding_findings) > 0
    assert len(rt_both.embedding_conclusion) == len(rt_both.embedding_findings)
    assert np.isclose(float(np.linalg.norm(np.asarray(rt_both.embedding_findings, dtype=np.float32))), 1.0, atol=1e-5)
    assert np.isclose(float(np.linalg.norm(np.asarray(rt_both.embedding_conclusion, dtype=np.float32))), 1.0, atol=1e-5)

    # 2. Conclusion only (empty Findings) -> 0-element findings vector, non-empty conclusion vector
    report_conclusion_only = DiagnosticReport(
        id="dr-emb-conclusion-only",
        status="final",
        code={"text": "CT CHEST"},
        encounter={"reference": "Encounter/enc-dr-emb-1"},
        conclusion="No acute cardiopulmonary abnormality.",
    )
    rt_conc = dr_svc.parse_diagnostic_report(report_conclusion_only)
    res_conc = await dr_svc.compute_embeddings(rt_conc, test_id=test_id)
    assert res_conc.status == STATUS_OK
    assert len(rt_conc.embedding_findings) == 0
    assert len(rt_conc.embedding_conclusion) == len(rt_both.embedding_conclusion)

    # 3. Empty texts -> both vectors have length 0
    report_empty = DiagnosticReport(
        id="dr-emb-empty",
        status="final",
        code={"text": "CT CHEST"},
        encounter={"reference": "Encounter/enc-dr-emb-1"},
    )
    rt_empty = dr_svc.parse_diagnostic_report(report_empty)
    res_empty = await dr_svc.compute_embeddings(rt_empty, test_id=test_id)
    assert res_empty.status == STATUS_OK
    assert len(rt_empty.embedding_findings) == 0
    assert len(rt_empty.embedding_conclusion) == 0


@pytest.mark.anyio
async def test_embedding_schema_and_graph_register_idempotency_and_vector_query():
    """Verify embedding_schema.cypher applies cleanly and graph_register persists & queries embeddings."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    # 1. Ensure schema DDL (including embedding_schema.cypher) is applied
    graph_svc = GraphService()
    await graph_svc.init_schema_constraints(force=True)

    idx_rows = await neo4j_client.execute_query(
        """
        SHOW VECTOR INDEXES
        YIELD name, labelsOrTypes, properties, options
        WHERE name = $index_name
        RETURN name, labelsOrTypes, properties, options
        """,
        {"index_name": EMBEDDING_VECTOR_INDEX_NAME},
    )
    assert len(idx_rows) == 1
    assert idx_rows[0]["labelsOrTypes"] == ["Embedding"]
    assert idx_rows[0]["properties"] == ["vector"]
    idx_cfg = (idx_rows[0].get("options") or {}).get("indexConfig") or {}
    assert idx_cfg.get("vector.dimensions") == DEFAULT_EMBEDDING_VECTOR_DIMENSIONS
    assert str(idx_cfg.get("vector.similarity_function", "")).lower() == "cosine"

    test_id = "test_diagnostic_report_graph_register"
    fhir_svc = FhirService()
    llm_svc = LLMConfigService()
    dr_svc = DiagnosticReportService(llm_config_service=llm_svc)

    # Clean up prior test-generated data BEFORE the test (retain after test for inspection)
    await fhir_svc.cleanup_upserted_resources(test_id=test_id)

    grp = GroupNode(
        name="DR Register Clinic",
        code="DR_REG_CLINIC",
        group_type="FACILITY",
        is_active=True,
        test_id=test_id,
        ActedBy="pytest",
    )
    await neo4j_client.execute_query(
        """
        MERGE (g:Group {id: $id})
        SET g.name = $name,
            g.code = $code,
            g.group_type = $group_type,
            g.is_active = true,
            g.test_id = $test_id,
            g.Action = 'CREATE',
            g.ActedAt = datetime({timezone: 'UTC'}),
            g.ActedBy = 'pytest',
            g.CreatedAt = datetime({timezone: 'UTC'})
        """,
        {
            "id": grp.id,
            "name": grp.name,
            "code": grp.code,
            "group_type": grp.group_type,
            "test_id": test_id,
        },
    )

    await llm_svc.upsert_llm_config(
        LLMConfigCreateRequest(
            tag=DEFAULT_EMBEDDING_TAG,
            provider="ollama",
            model="nomic-embed-text:latest",
            url="http://localhost:11434",
            dimensions=DEFAULT_EMBEDDING_VECTOR_DIMENSIONS,
            assign_group=grp.id,
            test_id=test_id,
        ),
        acted_by="pytest",
    )

    enc_r4b = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-dr-reg-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        }
    )
    dr_r4b = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "dr-reg-1",
            "status": "final",
            "code": {"text": "CT CHEST"},
            "encounter": {"reference": "Encounter/enc-dr-reg-1"},
            "conclusion": "No acute cardiopulmonary abnormality.",
        }
    )
    upsert_res = await fhir_svc.upsert_relevant_resources(
        EncRelevantResources(encounter=enc_r4b, diagnostic_reports=[dr_r4b]),
        test_id=test_id,
        acted_by="pytest",
    )
    enc_node_id = next(n["id"] for n in upsert_res["nodes"] if n["label"] == "Encounter")
    dr_node_id = next(n["id"] for n in upsert_res["nodes"] if n["label"] == "DiagnosticReport")
    link_res = await fhir_svc.link_encounter_to_group(
        encounter_id=enc_node_id,
        group_id=grp.id,
        test_id=test_id,
        acted_by="pytest",
    )
    assert link_res["linked"] is True

    # Build two distinct 768-dim L2-normalized vectors
    vec_findings = [1.0] + [0.0] * (DEFAULT_EMBEDDING_VECTOR_DIMENSIONS - 1)
    vec_conclusion = [0.0, 1.0] + [0.0] * (DEFAULT_EMBEDDING_VECTOR_DIMENSIONS - 2)

    report = DiagnosticReport(
        id="dr-reg-1",
        status="final",
        code={"text": "CT CHEST"},
        encounter={"reference": "Encounter/enc-dr-reg-1"},
    )
    rt = ReportTexts(
        report=report,
        hasText=True,
        findings="Lungs are clear without consolidation.",
        conclusion="No acute cardiopulmonary abnormality.",
        embedding_findings=vec_findings,
        embedding_conclusion=vec_conclusion,
    )

    # 2. First registration -> CREATE two :Embedding nodes linked via :EMBEDS
    reg1 = dr_svc.graph_register(rt, test_id=test_id, acted_by="pytest")
    assert reg1.status == STATUS_OK
    assert reg1.value is not None and len(reg1.value) == 2

    rows = await neo4j_client.execute_query(
        """
        MATCH (e:Embedding {test_id: $test_id})-[r:EMBEDS]->(dr:DiagnosticReport {id: $dr_id})
        RETURN e.id AS emb_id,
               e.modelTag AS model_tag,
               e.size AS size,
               e.targetFieldTag AS field_tag,
               e.Action AS emb_action,
               r.id AS rel_id,
               r.aspect AS aspect,
               r.Action AS rel_action
        ORDER BY e.targetFieldTag
        """,
        {"test_id": test_id, "dr_id": dr_node_id},
    )
    assert len(rows) == 2
    assert [r["field_tag"] for r in rows] == [
        DiagnosticReportFieldTag.CONCLUSION,
        DiagnosticReportFieldTag.FINDINGS,
    ]
    assert [r["aspect"] for r in rows] == [
        DiagnosticReportFieldTag.CONCLUSION,
        DiagnosticReportFieldTag.FINDINGS,
    ]
    assert all(r["model_tag"] == DEFAULT_EMBEDDING_TAG for r in rows)
    assert all(r["size"] == DEFAULT_EMBEDDING_VECTOR_DIMENSIONS for r in rows)
    assert all(r["emb_action"] == "CREATE" for r in rows)
    assert all(r["rel_action"] == "CREATE" for r in rows)

    # 3. Re-registration -> strictly idempotent (same IDs, no duplicates, Action='UPDATE')
    reg2 = dr_svc.graph_register(rt, test_id=test_id, acted_by="pytest")
    assert reg2.status == STATUS_OK
    assert reg2.value is not None
    assert {e.id for e in reg2.value} == {e.id for e in reg1.value}

    rows_after = await neo4j_client.execute_query(
        """
        MATCH (e:Embedding {test_id: $test_id})-[r:EMBEDS]->(dr:DiagnosticReport {id: $dr_id})
        RETURN e.id AS emb_id, e.Action AS emb_action, e.UpdatedAt AS updated_at, r.Action AS rel_action
        """,
        {"test_id": test_id, "dr_id": dr_node_id},
    )
    assert len(rows_after) == 2
    assert all(r["emb_action"] == "UPDATE" for r in rows_after)
    assert all(r["rel_action"] == "UPDATE" for r in rows_after)
    assert all(r["updated_at"] is not None for r in rows_after)

    # 4. Tenancy traversal from :Embedding anchor node -> :DiagnosticReport -> :Encounter -> :Group
    resolved_grp = dr_svc.group_service.my_group_sync(reg1.value[0].id, return_lost_and_found_group=False)
    assert resolved_grp is not None
    assert resolved_grp.id == grp.id

    # 5. Vector index query returns the persisted :Embedding node
    vec_hits = await neo4j_client.execute_query(
        """
        CALL db.index.vector.queryNodes($index_name, 5, $query_vec)
        YIELD node, score
        WHERE node.test_id = $test_id
        MATCH (node)-[r:EMBEDS]->(dr:DiagnosticReport)
        RETURN node.id AS emb_id, node.targetFieldTag AS field_tag, r.aspect AS aspect, dr.id AS dr_id, score
        """,
        {
            "index_name": EMBEDDING_VECTOR_INDEX_NAME,
            "query_vec": vec_findings,
            "test_id": test_id,
        },
    )
    assert len(vec_hits) >= 1
    assert vec_hits[0]["field_tag"] == DiagnosticReportFieldTag.FINDINGS
    assert vec_hits[0]["aspect"] == DiagnosticReportFieldTag.FINDINGS
    assert vec_hits[0]["dr_id"] == dr_node_id
    assert vec_hits[0]["score"] >= 0.99


@pytest.mark.anyio
async def test_embedding_actor_end_to_end_diagnostic_report_pipeline():
    """Verify EmbeddingActor computes embeddings and registers :Embedding -[:EMBEDS]-> :DiagnosticReport in Neo4j."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    llm_svc = LLMConfigService()
    emb_svc = EmbeddingService()
    active_llm_cfg = await llm_svc.resolve_config_for_group(tag=DEFAULT_EMBEDDING_TAG)
    if active_llm_cfg is None or not emb_svc.is_configured(llm_config=active_llm_cfg):
        pytest.skip(f"No active usable '{DEFAULT_EMBEDDING_TAG}' LLM_Config configured in Neo4j; skipping live test.")

    try:
        await emb_svc.embed_text_async("Health check.", llm_config=active_llm_cfg)
    except Exception as exc:
        pytest.skip(f"Configured embedding provider is not reachable: {exc}")

    graph_svc = GraphService()
    await graph_svc.init_schema_constraints(force=True)

    test_id = "test_embedding_actor_e2e"
    fhir_svc = FhirService()

    # Clean up prior test-generated data BEFORE the test (retain after test for inspection)
    await fhir_svc.cleanup_upserted_resources(test_id=test_id)

    grp = GroupNode(
        name="Embedding Actor Clinic",
        code="EMB_ACTOR_CLINIC",
        group_type="FACILITY",
        is_active=True,
        test_id=test_id,
        ActedBy="pytest",
    )
    await neo4j_client.execute_query(
        """
        MERGE (g:Group {id: $id})
        SET g.name = $name,
            g.code = $code,
            g.group_type = $group_type,
            g.is_active = true,
            g.test_id = $test_id,
            g.Action = 'CREATE',
            g.ActedAt = datetime({timezone: 'UTC'}),
            g.ActedBy = 'pytest',
            g.CreatedAt = datetime({timezone: 'UTC'})
        """,
        {
            "id": grp.id,
            "name": grp.name,
            "code": grp.code,
            "group_type": grp.group_type,
            "test_id": test_id,
        },
    )

    await llm_svc.upsert_llm_config(
        LLMConfigCreateRequest(
            tag=DEFAULT_EMBEDDING_TAG,
            provider=active_llm_cfg.provider,
            model=active_llm_cfg.model,
            url=active_llm_cfg.url,
            api_key=active_llm_cfg.api_key,
            dimensions=active_llm_cfg.dimensions,
            dimensions_truncated=active_llm_cfg.dimensions_truncated,
            project_id=active_llm_cfg.project_id,
            location=active_llm_cfg.location,
            extra_config=active_llm_cfg.extra_config,
            assign_group=grp.id,
            test_id=test_id,
        ),
        acted_by="pytest",
    )

    report_text = "Findings: Mild cardiomegaly without acute pulmonary edema.\nImpression: Mild cardiomegaly."
    enc_r4b = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-emb-actor-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        }
    )
    dr_r4b = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "dr-emb-actor-1",
            "status": "final",
            "code": {"text": "CXR PA/LAT"},
            "encounter": {"reference": "Encounter/enc-emb-actor-1"},
            "presentedForm": [
                {
                    "contentType": "text/plain",
                    "data": base64.b64encode(report_text.encode("utf-8")).decode("ascii"),
                }
            ],
        }
    )
    upsert_res = await fhir_svc.upsert_relevant_resources(
        EncRelevantResources(encounter=enc_r4b, diagnostic_reports=[dr_r4b]),
        test_id=test_id,
        acted_by="pytest",
    )
    enc_node_id = next(n["id"] for n in upsert_res["nodes"] if n["label"] == "Encounter")
    dr_node_id = next(n["id"] for n in upsert_res["nodes"] if n["label"] == "DiagnosticReport")
    link_res = await fhir_svc.link_encounter_to_group(
        encounter_id=enc_node_id,
        group_id=grp.id,
        test_id=test_id,
        acted_by="pytest",
    )
    assert link_res["linked"] is True

    actor = EmbeddingActor.start()
    try:
        group_ctx = Group(id=grp.id, name=grp.name, code=grp.code, group_type=grp.group_type, is_active=True)
        res1 = actor.ask(
            EmbeddingMessage(
                method=EmbeddingMethod.DIAGNOSTIC_REPORT,
                resource=dr_r4b,
                group_context=group_ctx,
                test_id=test_id,
                acted_by="pytest",
            ),
            timeout=30.0,
        )
        assert res1["success"] is True
        assert res1["method"] == str(EmbeddingMethod.DIAGNOSTIC_REPORT)
        assert res1["report_id"] == "dr-emb-actor-1"
        assert res1["registered_count"] == 2
        assert len(res1["embedding_ids"]) == 2

        # Re-run via group_id fallback (without explicit group_context) to verify idempotency
        res2 = actor.ask(
            EmbeddingMessage(
                method=EmbeddingMethod.DIAGNOSTIC_REPORT,
                resource=dr_r4b,
                group_id=grp.id,
                test_id=test_id,
                acted_by="pytest",
            ),
            timeout=30.0,
        )
        assert res2["success"] is True
        assert set(res2["embedding_ids"]) == set(res1["embedding_ids"])
    finally:
        actor.stop()

    rows = await neo4j_client.execute_query(
        """
        MATCH (e:Embedding {test_id: $test_id})-[r:EMBEDS]->(dr:DiagnosticReport {id: $dr_id})
        RETURN e.id AS emb_id, e.targetFieldTag AS field_tag, e.modelTag AS model_tag,
               e.size AS size, size(e.vector) AS vec_len, r.aspect AS aspect
        ORDER BY e.targetFieldTag
        """,
        {"test_id": test_id, "dr_id": dr_node_id},
    )
    assert len(rows) == 2
    assert [r["field_tag"] for r in rows] == [
        DiagnosticReportFieldTag.CONCLUSION,
        DiagnosticReportFieldTag.FINDINGS,
    ]
    assert [r["aspect"] for r in rows] == [
        DiagnosticReportFieldTag.CONCLUSION,
        DiagnosticReportFieldTag.FINDINGS,
    ]
    assert all(r["model_tag"] == DEFAULT_EMBEDDING_TAG for r in rows)
    assert all(r["size"] == r["vec_len"] and r["size"] > 0 for r in rows)
