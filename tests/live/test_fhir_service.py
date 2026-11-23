"""Live FHIR proxy tests, including optional Neo4j upsert verification."""

from typing import Any

import httpx
import pytest

from gosmart_graphite.core.config import get_settings
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.service.fhir_service import FhirService
from tests.utils.config_utils import ConfigUtils


def _is_fhir_proxy_live() -> bool:
    """Check whether the configured FHIR proxy is responsive."""
    base_url = get_settings().FHIR_BASE_URL.rstrip("/")
    if not base_url:
        return False
    try:
        with httpx.Client(timeout=2.0) as client:
            return client.get(f"{base_url}/metadata").status_code == 200
    except Exception as why:
        print(why)
        return False


FHIR_PROXY_LIVE = _is_fhir_proxy_live()
all_configs = ConfigUtils().configs


async def cleanup_fhir_test_graph(
    svc: FhirService,
    test_id: str,
    node_ids: list[str],
    relationship_ids: list[str],
    enabled: bool = all_configs["cleanup_enable"],
) -> dict[str, int]:
    """Clean up only resources created by the current test, when enabled."""
    if not enabled:
        return {"deleted_nodes": 0, "deleted_relationships": 0}
    return await svc.cleanup_upserted_resources(
        test_id=test_id,
        node_ids=node_ids,
        relationship_ids=relationship_ids,
    )


# =====================================================================
# Live Integration Tests (against running FHIR proxy)
# =====================================================================


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_encounter(test_config: dict[str, Any]):
    """
    Fetch live encounter 3033 from HAPI FHIR proxy and validate fields.
    Please see tests/configs.json for test_config values and conftest.py where
    the pytest fixture "test_config" is defined..
    IMPORTANT:
    IF YOU GENERATE new HAPI FHIR records you need to find sutiable
    Live configurations.
    """
    svc = FhirService()
    enc = await svc.fetch_encounter(test_config["encounter.id"])
    assert enc is not None
    assert enc.id == test_config["encounter.id"]
    assert enc.status == test_config["encounter.status"]
    assert enc.subject is not None
    assert enc.subject.reference == test_config["subject.reference"]


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_patient(test_config: dict[str, Any]):
    """Fetch live Patient 2977 from HAPI FHIR proxy."""
    svc = FhirService()
    patient = await svc.fetch_patient(test_config["subject.reference"])
    assert patient is not None
    assert patient.id == test_config["subject.id"]


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_imaging_study_by_uid(test_config: dict[str, Any]):
    """Fetch live ImagingStudy using DICOM Study Instance UID and urn:oid prefix."""
    svc = FhirService()
    study_uid = test_config["study_uid"]

    # 1. Bare UID
    study = await svc.fetch_imaging_study(study_uid)
    assert study is not None
    assert study.id == test_config["study.id"]
    assert study.status == test_config["study.status"]

    # 2. With urn:oid: prefix
    study_urn = await svc.fetch_imaging_study(f"urn:oid:{study_uid}")
    assert study_urn is not None
    assert study_urn.id == test_config["study.id"]


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_diagnostic_report(test_config: dict[str, Any]):
    """Fetch live DiagnosticReport for encounter 3033."""
    svc = FhirService()
    report = await svc.fetch_enc_diagnostic_report(test_config["encounter.id"])
    assert report is not None
    assert report.id == test_config["report.id"]
    assert report.status == test_config["report.status"]
    assert report.encounter is not None
    assert report.encounter.reference == test_config["encounter.reference"]


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_enc_relevant_resources(test_config: dict[str, Any]):
    """Fetch entire encounter 3033 resource graph concurrently from proxy."""
    svc = FhirService()
    resources = await svc.fetch_enc_relevant_resources(test_config["encounter.id"])

    assert resources.encounter is not None
    assert resources.encounter.id == test_config["encounter.id"]
    assert resources.patient is not None
    assert resources.patient.id == test_config["patient.id"]

    assert ConfigUtils.compare(len(resources.diagnostic_reports), test_config["diagnostic.reports.length"])
    assert resources.diagnostic_reports[0].id == test_config["diagnostic.reports.id_0"]

    assert ConfigUtils.compare(len(resources.imaging_studies), test_config["imaging_studies.length"])
    assert resources.imaging_studies[0].id == test_config["imaging_studies.id_0"]

    assert ConfigUtils.compare(len(resources.procedures), test_config["procedures.length"])
    assert ConfigUtils.compare(len(resources.errors), test_config["errors.length"])


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_and_upsert_study_790735190745_twice(test_config: dict[str, Any]):
    """Fetch live study 1.2.840.99999999.40196652.790735190745 and verify CREATE then UNCHANGED no-op."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    test_id = "test_fhir_study_790735190745"
    fhir = FhirService()

    # Ensure clean slate prior to test run
    await fhir.cleanup_upserted_resources(test_id=test_id)

    resources = await fhir.fetch_relevant_resources_of_study(test_config["study_uid"])
    assert resources.encounter is not None
    assert resources.patient is not None
    assert ConfigUtils.compare(len(resources.imaging_studies), test_config["imaging_studies.length"])
    assert ConfigUtils.compare(len(resources.diagnostic_reports), test_config["diagnostic_reports.length"])
    assert ConfigUtils.compare(len(resources.observations), test_config["observations.length"])
    assert ConfigUtils.compare(len(resources.procedures), test_config["procedures.length"])

    upserted_node_ids: list[str] = []
    upserted_rel_ids: list[str] = []

    try:
        # 1. First pass: Inserting (Action == "CREATE")
        first_result = await fhir.upsert_relevant_resources(
            resources,
            test_id=test_id,
            acted_by="pytest_first_pass",
        )
        upserted_node_ids = first_result["node_ids"]
        upserted_rel_ids = first_result["relationship_ids"]

        assert len(upserted_node_ids) > 0
        assert len(upserted_rel_ids) > 0
        assert all(n["action"] == "CREATE" for n in first_result["nodes"])
        assert all(r["action"] == "CREATE" for r in first_result["relationships"])

        # Verify nodes exist in Neo4j with Action == "CREATE" and CreatedAt set
        db_create_check = await neo4j_client.execute_query(
            """
            MATCH (n {test_id: $test_id})
            RETURN count(n) AS cnt,
                   collect(distinct n.Action) AS actions,
                   count(n.CreatedAt) AS created_cnt
            """,
            {"test_id": test_id},
        )
        assert db_create_check[0]["cnt"] == len(upserted_node_ids)
        assert db_create_check[0]["actions"] == ["CREATE"]
        assert db_create_check[0]["created_cnt"] == len(upserted_node_ids)

        # 2. Second pass: Identical resources -> idempotent no-op (Action remains "CREATE", returns "UNCHANGED")
        second_result = await fhir.upsert_relevant_resources(
            resources,
            test_id=test_id,
            acted_by="pytest_second_pass",
        )
        assert second_result["node_ids"] == upserted_node_ids
        assert second_result["relationship_ids"] == upserted_rel_ids
        assert all(n["action"] == "UNCHANGED" for n in second_result["nodes"])
        assert all(r["action"] == "UNCHANGED" for r in second_result["relationships"])

        # Verify in Neo4j that no duplicates were created and Action remains "CREATE" with UpdatedAt null
        db_update_check = await neo4j_client.execute_query(
            """
            MATCH (n {test_id: $test_id})
            RETURN count(n) AS cnt,
                   collect(distinct n.Action) AS actions,
                   count(n.UpdatedAt) AS updated_cnt
            """,
            {"test_id": test_id},
        )
        assert db_update_check[0]["cnt"] == len(upserted_node_ids)
        assert db_update_check[0]["actions"] == ["CREATE"]
        assert db_update_check[0]["updated_cnt"] == 0

        rel_update_check = await neo4j_client.execute_query(
            """
            MATCH ()-[r {test_id: $test_id}]->()
            RETURN count(r) AS cnt,
                   collect(distinct r.Action) AS actions,
                   count(r.UpdatedAt) AS updated_cnt
            """,
            {"test_id": test_id},
        )
        assert rel_update_check[0]["cnt"] == len(upserted_rel_ids)
        assert rel_update_check[0]["actions"] == ["CREATE"]
        assert rel_update_check[0]["updated_cnt"] == 0
    finally:
        # Separate disposal function (can be disabled via "cleanup_enable": false in tests/configs.json)
        cleanup_stats = await cleanup_fhir_test_graph(
            fhir,
            test_id=test_id,
            node_ids=upserted_node_ids,
            relationship_ids=upserted_rel_ids,
            enabled=all_configs["cleanup_enable"],
        )
        if all_configs["cleanup_enable"]:
            assert cleanup_stats["deleted_nodes"] == len(upserted_node_ids)
            remaining = await neo4j_client.execute_query(
                "MATCH (n {test_id: $test_id}) RETURN count(n) AS cnt",
                {"test_id": test_id},
            )
            assert remaining[0]["cnt"] == 0
