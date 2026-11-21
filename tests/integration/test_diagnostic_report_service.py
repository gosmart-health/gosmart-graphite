"""Integration tests for DiagnosticReportService.config_ensure against Neo4j."""

import pytest
from fhir.resources.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.diagnosticreport import DiagnosticReport as DiagnosticReportR4B
from fhir.resources.R4B.encounter import Encounter

from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model.auth import GroupNode
from gosmart_graphite.model.llm_config import LLMConfigCreateRequest
from gosmart_graphite.model.result import STATUS_BAD_DATA, STATUS_OK
from gosmart_graphite.service.diagnostic_report_service import DiagnosticReportService, ReportTexts
from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG
from gosmart_graphite.service.fhir_service import EncRelevantResources, FhirService
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
    dr_r4b = DiagnosticReportR4B.model_validate(
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
