"""Integration tests for DICOM hierarchy persistence in Neo4j."""

from pathlib import Path

import pydicom
import pytest
from pydicom.uid import generate_uid

from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.graph_service import graph_service
from tests.utils.config_utils import ConfigUtils

TEST_ID = "test_dicom_mr_hierarchy"
TEST_PATIENT_ID = "TEST-DICOM-MR-HIERARCHY"
TEST_ASSETS_DIR = ConfigUtils.assets_root() / "mr"


def _clone_mr_assets(src_dir: Path, dst_dir: Path) -> list[Path]:
    """Copy MR DICOM assets into an isolated directory with test-scoped identifiers."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    study_uid = generate_uid(entropy_srcs=[TEST_ID, "study"])
    series_uid = generate_uid(entropy_srcs=[TEST_ID, "series"])

    cloned_files: list[Path] = []
    for src_path in sorted(src_dir.glob("*.dcm")):
        ds = pydicom.dcmread(str(src_path), force=True)
        sop_uid = generate_uid(entropy_srcs=[TEST_ID, src_path.name])
        ds.PatientID = TEST_PATIENT_ID
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.SOPInstanceUID = sop_uid
        if hasattr(ds, "file_meta") and ds.file_meta:
            ds.file_meta.MediaStorageSOPInstanceUID = sop_uid

        dst_path = dst_dir / src_path.name
        ds.save_as(str(dst_path))
        cloned_files.append(dst_path)
    return cloned_files


# Integration Tests (executed against Neo4j if reachable)


@pytest.mark.anyio
async def test_dicom_mr_ingestion_and_hierarchy_traversal(tmp_path: Path):
    """Load 10 MR DICOM images, ingest into Neo4j graph, verify hierarchy traversal and audit fields."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    # Ensure schema constraints are created
    await graph_service.init_schema_constraints()

    # Per AGENTS.md rule: Clean up pre-existing test data BEFORE the test, not after
    await graph_service.cleanup_test_data(TEST_ID)

    # 1. Discover and isolate all MR DICOM files in a test-specific directory
    assert TEST_ASSETS_DIR.exists(), f"Test assets directory not found: {TEST_ASSETS_DIR}"
    dicom_files = _clone_mr_assets(TEST_ASSETS_DIR, tmp_path / "mr")
    assert len(dicom_files) == 10, f"Expected 10 MR test files, found {len(dicom_files)}"

    # 2. Parse attributes and store into Neo4j graph with explicit test_id
    parsed_sop_uids = set()
    for dcm_path in dicom_files:
        hierarchy = dicom_service.parse_file(dcm_path)
        hierarchy.test_id = TEST_ID
        hierarchy.acted_by = "test_agent"
        assert hierarchy.patient.patient_id == TEST_PATIENT_ID
        parsed_sop_uids.add(hierarchy.instance.sop_instance_uid)

        saved_record = await graph_service.save_hierarchy(hierarchy)
        assert saved_record.get("patient_id") == TEST_PATIENT_ID
        assert saved_record.get("sop_instance_uid") == hierarchy.instance.sop_instance_uid
        assert saved_record.get("p_id") is not None

    assert len(parsed_sop_uids) == 10, "Expected 10 distinct SOP Instance UIDs from parsed files"

    # 3. Verify single label and auditable properties on Patient node
    verify_patient_query = """
    MATCH (p:Patient {patient_id: $patient_id, test_id: $test_id})
    RETURN p.id AS id,
           p.test_id AS test_id,
           p.Action AS Action,
           p.ActedBy AS ActedBy,
           p.CreatedAt AS CreatedAt,
           p.ExpiresAt AS ExpiresAt,
           labels(p) AS labels
    """
    records = await graph_service.execute_custom_query(
        verify_patient_query, {"patient_id": TEST_PATIENT_ID, "test_id": TEST_ID}
    )
    assert len(records) == 1
    p_rec = records[0]
    patient_node_id = p_rec["id"]
    assert p_rec["test_id"] == TEST_ID
    assert p_rec["Action"] in ("CREATE", "UPDATE")
    assert p_rec["ActedBy"] == "test_agent"
    assert p_rec["CreatedAt"] is not None
    assert p_rec["labels"] == ["Patient"], "Single label rule: node must only have label :Patient"

    # 4. Check graph traversal from Patient -> Study -> Series -> Instance
    traversal_query = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_STUDY]->(s:Study)
          -[:HAS_SERIES]->(se:Series)-[:HAS_INSTANCE]->(i:Instance)
    RETURN p.id AS p_id,
           p.patient_id AS patient_id,
           p.patient_name AS patient_name,
           s.id AS study_id,
           s.study_instance_uid AS study_uid,
           se.series_instance_uid AS series_uid,
           se.modality AS modality,
           count(i) AS instance_count,
           collect(i.sop_instance_uid) AS instance_uids
    """
    traversal_results = await graph_service.execute_custom_query(traversal_query, {"patient_id": patient_node_id})
    assert len(traversal_results) == 1
    result = traversal_results[0]
    study_node_id = result["study_id"]
    assert result["patient_id"] == TEST_PATIENT_ID
    assert result["modality"] == "MR"
    assert result["instance_count"] == 10
    assert set(result["instance_uids"]) == parsed_sop_uids

    # 4b. Verify Instance-level stream provenance tracking in Neo4j
    instance_provenance_query = """
    MATCH (i:Instance {test_id: $test_id})
    RETURN i.Source AS Source,
           i.SourceIdentity AS SourceIdentity,
           i.Destination AS Destination,
           i.DestinationIdentity AS DestinationIdentity
    """
    inst_records = await graph_service.execute_custom_query(instance_provenance_query, {"test_id": TEST_ID})
    assert len(inst_records) == 10
    for rec in inst_records:
        assert rec["Source"].startswith("file:")
        assert rec["SourceIdentity"] == "user:local"
        assert rec["Destination"].startswith("file:")
        assert rec["DestinationIdentity"] == "service:local_storage"

    # Verify that Patient, Study, Series do NOT have Source or Destination properties in graph
    non_instance_query = """
    MATCH (p:Patient {id: $patient_id})-[:HAS_STUDY]->(s:Study)-[:HAS_SERIES]->(se:Series)
    RETURN p.Source AS p_source, p.Destination AS p_dest,
           s.Source AS s_source, s.Destination AS s_dest,
           se.Source AS se_source, se.Destination AS se_dest
    """
    non_inst_records = await graph_service.execute_custom_query(non_instance_query, {"patient_id": patient_node_id})
    assert len(non_inst_records) == 1
    ni = non_inst_records[0]
    assert ni["p_source"] is None and ni["p_dest"] is None
    assert ni["s_source"] is None and ni["s_dest"] is None
    assert ni["se_source"] is None and ni["se_dest"] is None

    # 5. Verify query filtering: default list_patients excludes test_id
    default_patients = await graph_service.list_patients(include_tests=False)
    assert not any(p["id"] == patient_node_id for p in default_patients), "Default queries must exclude test data"

    test_patients = await graph_service.list_patients(include_tests=True, test_id=TEST_ID)
    assert any(p["id"] == patient_node_id for p in test_patients), "include_tests=True must return test data"

    # 6. Test Transactional Relationship Soft-Deletion: HAS_STUDY -> NOT_HAS_STUDY
    deleted_rel = await graph_service.soft_delete_relationship(
        start_node_id=patient_node_id,
        end_node_id=study_node_id,
        rel_type="HAS_STUDY",
        acted_by="audit_tester",
    )
    assert deleted_rel is not None
    assert deleted_rel["Action"] == "DELETE"
    assert deleted_rel["ActedBy"] == "audit_tester"

    # Check that traversal now returns 0 active studies (since HAS_STUDY is substituted by NOT_HAS_STUDY)
    traversal_after_soft_delete = await graph_service.execute_custom_query(
        traversal_query, {"patient_id": patient_node_id}
    )
    assert len(traversal_after_soft_delete) == 0, "Soft-deleted relation must not match active traversal"

    # Verify NOT_HAS_STUDY exists in Neo4j with cloned properties
    check_not_rel_query = """
    MATCH (p {id: $p_id})-[r:NOT_HAS_STUDY]->(s {id: $s_id})
    RETURN r.id AS id, r.Action AS Action, r.ActedBy AS ActedBy, r.CreatedAt AS CreatedAt
    """
    not_records = await graph_service.execute_custom_query(
        check_not_rel_query, {"p_id": patient_node_id, "s_id": study_node_id}
    )
    assert len(not_records) == 1
    assert not_records[0]["Action"] == "DELETE"
    assert not_records[0]["CreatedAt"] is not None

    # 7. Test Transactional Relationship Restoration: NOT_HAS_STUDY -> HAS_STUDY
    restored_rel = await graph_service.restore_relationship(
        start_node_id=patient_node_id,
        end_node_id=study_node_id,
        rel_type="HAS_STUDY",
        acted_by="audit_tester",
    )
    assert restored_rel is not None
    assert restored_rel["Action"] == "RESTORE"

    # Traversal should now succeed again
    traversal_after_restore = await graph_service.execute_custom_query(traversal_query, {"patient_id": patient_node_id})
    assert len(traversal_after_restore) == 1
    assert traversal_after_restore[0]["instance_count"] == 10

    # 8. Test Node Soft-Deletion & Restoration
    soft_del_node_res = await graph_service.soft_delete_node(patient_node_id, acted_by="audit_tester")
    assert soft_del_node_res is not None
    assert soft_del_node_res["Action"] == "DELETE"

    # Traversal should now exclude soft-deleted patient
    active_graph = await graph_service.get_patient_graph(patient_node_id, include_tests=True)
    assert not active_graph, "Soft-deleted node must be excluded from get_patient_graph"

    # Restore node
    restore_node_res = await graph_service.restore_node(patient_node_id, acted_by="audit_tester")
    assert restore_node_res is not None
    assert restore_node_res["Action"] == "RESTORE"

    active_graph_restored = await graph_service.get_patient_graph(patient_node_id, include_tests=True)
    assert active_graph_restored, "Restored node must be retrieved by get_patient_graph"
