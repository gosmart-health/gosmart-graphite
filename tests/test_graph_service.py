from pathlib import Path

import pytest

from db.neo4j import neo4j_client
from service.dicom_service import dicom_service
from service.graph_service import graph_service

TEST_PATIENT_ID = "GSH-90967971"
TEST_ASSETS_DIR = Path(__file__).parent / "assets" / "mr"


async def cleanup_test_patient(patient_id: str) -> None:
    """Detach and delete the test patient and all associated studies, series, and instances."""
    cleanup_query = """
    MATCH (p:Patient {patient_id: $patient_id})
    OPTIONAL MATCH (p)-[:HAS_STUDY]->(s:Study)
    OPTIONAL MATCH (s)-[:HAS_SERIES]->(se:Series)
    OPTIONAL MATCH (se)-[:HAS_INSTANCE]->(i:Instance)
    DETACH DELETE i, se, s, p
    """
    await graph_service.execute_custom_query(cleanup_query, {"patient_id": patient_id})


@pytest.mark.anyio
async def test_dicom_mr_ingestion_and_hierarchy_traversal():
    """Load 10 MR DICOM images, ingest into Neo4j graph, and verify hierarchy traversal."""
    # Ensure Neo4j connectivity before proceeding with integration test
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    # Ensure schema constraints are created
    await graph_service.init_schema_constraints()

    # Clean up any pre-existing test data
    await cleanup_test_patient(TEST_PATIENT_ID)

    try:
        # 1. Discover all MR DICOM files
        assert TEST_ASSETS_DIR.exists(), f"Test assets directory not found: {TEST_ASSETS_DIR}"
        dicom_files = sorted(TEST_ASSETS_DIR.glob("*.dcm"))
        assert len(dicom_files) == 10, f"Expected 10 MR test files, found {len(dicom_files)}"

        # 2. Parse attributes and store into Neo4j graph
        parsed_sop_uids = set()
        for dcm_path in dicom_files:
            hierarchy = dicom_service.parse_file(dcm_path)
            assert hierarchy.patient.patient_id == TEST_PATIENT_ID
            parsed_sop_uids.add(hierarchy.instance.sop_instance_uid)

            saved_record = await graph_service.save_hierarchy(hierarchy)
            assert saved_record.get("patient_id") == TEST_PATIENT_ID
            assert saved_record.get("sop_instance_uid") == hierarchy.instance.sop_instance_uid

        assert len(parsed_sop_uids) == 10, "Expected 10 distinct SOP Instance UIDs from parsed files"

        # 3. Check graph traversal from Patient -> Study -> Series -> Instance
        traversal_query = """
        MATCH (p:Patient {patient_id: $patient_id})-[:HAS_STUDY]->(s:Study)
              -[:HAS_SERIES]->(se:Series)-[:HAS_INSTANCE]->(i:Instance)
        RETURN p.patient_id AS patient_id,
               p.patient_name AS patient_name,
               s.study_instance_uid AS study_uid,
               se.series_instance_uid AS series_uid,
               se.modality AS modality,
               count(i) AS instance_count,
               collect(i.sop_instance_uid) AS instance_uids
        """
        traversal_results = await graph_service.execute_custom_query(traversal_query, {"patient_id": TEST_PATIENT_ID})

        assert len(traversal_results) == 1, "Expected exactly 1 series traversal result for the patient"
        result = traversal_results[0]

        assert result["patient_id"] == TEST_PATIENT_ID
        assert result["patient_name"] == "ADAMS_GSH^DANIEL"
        assert result["study_uid"] == "2.25.76414732282826892573000751406246898944"
        assert result["series_uid"] == "2.25.25880016922851921110766408171533417726"
        assert result["modality"] == "MR"
        assert result["instance_count"] == 10

        traversed_instance_uids = set(result["instance_uids"])
        assert traversed_instance_uids == parsed_sop_uids, "Traversed instance UIDs must match all parsed SOP UIDs"

        # 4. Verify get_patient_graph method retrieves the full hierarchy
        patient_graph = await graph_service.get_patient_graph(TEST_PATIENT_ID)
        assert patient_graph, "get_patient_graph should return a populated hierarchy"
        assert patient_graph["p"]["patient_id"] == TEST_PATIENT_ID
        assert len(patient_graph["studies"]) == 1
        assert len(patient_graph["series"]) == 1
        assert len(patient_graph["instances"]) == 10

    finally:
        # Tear down test data
        await cleanup_test_patient(TEST_PATIENT_ID)
