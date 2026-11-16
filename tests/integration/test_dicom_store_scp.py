import socket
from unittest.mock import patch

import pydicom
import pytest
from pydicom.uid import generate_uid
from pynetdicom import AE

from gosmart_graphite.core.config import get_settings
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.media_service import media_service
from gosmart_graphite.service.pynetdicom_service import PyNetDicomService
from tests.utils.config_utils import ConfigUtils

GOSMART_GRAPHITE = "GOSMART_GRAPHITE"
TEST_ID = "test_dicom_store_scp"
TEST_PATIENT_ID = "TEST-DICOM-STORE-SCP"
TEST_ASSET_DCM = ConfigUtils.assets_root() / "mr/MR.2.25.10986282349447326157620829373816019196.dcm"


def get_free_port() -> int:
    """Find a dynamically available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_isolated_dataset(suffix: str) -> pydicom.Dataset:
    """Load the MR test asset with deterministic test-scoped identifiers."""
    ds = pydicom.dcmread(TEST_ASSET_DCM)
    sop_uid = generate_uid(entropy_srcs=[TEST_ID, suffix, "sop"])
    ds.PatientID = TEST_PATIENT_ID
    ds.StudyInstanceUID = generate_uid(entropy_srcs=[TEST_ID, suffix, "study"])
    ds.SeriesInstanceUID = generate_uid(entropy_srcs=[TEST_ID, suffix, "series"])
    ds.SOPInstanceUID = sop_uid
    if hasattr(ds, "file_meta") and ds.file_meta:
        ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
    return ds


def _is_neo4j_reachable() -> bool:
    """Return True if the configured Neo4j instance is reachable."""
    try:
        neo4j_client.get_sync_driver().verify_connectivity()
        return True
    except Exception:
        return False


def _tag_hierarchy_test_id(sop_uid: str, test_id: str) -> None:
    """Tag the Patient -> Study -> Series -> Instance hierarchy with test_id for cleanup."""
    driver = neo4j_client.get_sync_driver()
    with driver.session(database=neo4j_client.settings.DB_DATABASE) as session:
        session.run(
            """
            MATCH (p:Patient)-[r1:HAS_STUDY]->(s:Study)-[r2:HAS_SERIES]->(se:Series)
                  -[r3:HAS_INSTANCE]->(i:Instance {sop_instance_uid: $sop_uid})
            SET p.test_id = $test_id,
                s.test_id = $test_id,
                se.test_id = $test_id,
                i.test_id = $test_id,
                r1.test_id = $test_id,
                r2.test_id = $test_id,
                r3.test_id = $test_id
            """,
            sop_uid=sop_uid,
            test_id=test_id,
        )


@pytest.fixture
def dicom_server():
    """Fixture that starts a temporary PyNetDicomService SCP on a free port."""
    settings = get_settings()
    port = get_free_port()
    service = PyNetDicomService(settings=settings)
    service.start_server(host="127.0.0.1", port=port, block=False)
    yield service, port
    service.stop_server()


def test_dicom_c_echo_success(dicom_server):
    """Test C-ECHO verification against running SCP server."""
    service, port = dicom_server
    assert service.is_running()

    result = service.echo_scu(host="127.0.0.1", port=port, called_aet=GOSMART_GRAPHITE)
    assert result["success"] is True
    assert result["status_code"] == "0x0"


def test_dicom_c_store_blackbox_persistence(dicom_server):
    """Test C-STORE SCU -> SCP transfer preserves raw blackbox file and indexes graph."""
    service, port = dicom_server
    settings = get_settings()

    neo4j_reachable = _is_neo4j_reachable()
    if neo4j_reachable:
        # Per AGENTS.md rule: Clean up pre-existing test data BEFORE the test, not after
        graph_service.cleanup_test_data_sync(TEST_ID)

    assert TEST_ASSET_DCM.exists(), f"Asset missing: {TEST_ASSET_DCM}"
    ds = _build_isolated_dataset("blackbox")
    sop_uid = str(ds.SOPInstanceUID)
    expected_storage_uri = f"{settings.STORAGE_ROOT_URI.rstrip('/')}/{sop_uid}.dcm"

    # Pre-clean storage if exists
    if media_service.exists_sync(expected_storage_uri):
        media_service.delete_sync(expected_storage_uri)

    # Send C-STORE using an SCU Application Entity
    client_ae = AE(ae_title="TEST_SCU")
    client_ae.add_requested_context(ds.SOPClassUID, ds.file_meta.TransferSyntaxUID)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=GOSMART_GRAPHITE)
    assert assoc.is_established, "SCU failed to establish association with SCP"

    status = assoc.send_c_store(ds)
    assoc.release()

    assert status is not None
    assert status.Status == 0x0000, f"Expected C-STORE SUCCESS (0x0000), got {status.Status}"

    # Verify FIRST ORDER OF ACTION: Blackbox persistence to storage
    assert media_service.exists_sync(expected_storage_uri), f"Blackbox file not saved at {expected_storage_uri}"

    # Read back and verify integrity
    stored_ds = media_service.read_dicom_sync(expected_storage_uri)
    assert stored_ds.SOPInstanceUID == ds.SOPInstanceUID
    assert stored_ds.PatientID == ds.PatientID

    # Verify Instance provenance tracking in Neo4j (if connected)
    if neo4j_reachable:
        _tag_hierarchy_test_id(sop_uid, TEST_ID)
        driver = neo4j_client.get_sync_driver()
        with driver.session(database=neo4j_client.settings.DB_DATABASE) as session:
            result = session.run(
                "MATCH (i:Instance {sop_instance_uid: $sop_uid}) "
                "RETURN i.Source AS Source, i.SourceIdentity AS SourceIdentity, "
                "i.Destination AS Destination, i.DestinationIdentity AS DestinationIdentity",
                sop_uid=sop_uid,
            )
            records = [record.data() for record in result]
        assert len(records) == 1
        inst = records[0]
        assert inst["Source"] == "dimse:127.0.0.1"
        assert inst["SourceIdentity"] == "ae:TEST_SCU"
        assert inst["Destination"].startswith("dimse:")
        assert inst["DestinationIdentity"] == f"ae:{GOSMART_GRAPHITE}"


def test_dicom_c_store_blackbox_preservation_on_downstream_failure(dicom_server):
    """Verify raw DICOM file is preserved on storage even if downstream graph indexing fails."""
    service, port = dicom_server
    settings = get_settings()

    ds = _build_isolated_dataset("failure")
    sop_uid = str(ds.SOPInstanceUID)
    expected_storage_uri = f"{settings.STORAGE_ROOT_URI.rstrip('/')}/{sop_uid}.dcm"

    # Pre-clean storage
    if media_service.exists_sync(expected_storage_uri):
        media_service.delete_sync(expected_storage_uri)

    client_ae = AE(ae_title="TEST_SCU")
    client_ae.add_requested_context(ds.SOPClassUID, ds.file_meta.TransferSyntaxUID)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=GOSMART_GRAPHITE)
    assert assoc.is_established

    # Simulate downstream Neo4j failure
    with patch(
        "gosmart_graphite.service.graph_service.graph_service.save_hierarchy_sync",
        side_effect=RuntimeError("Simulated DB failure"),
    ):
        status = assoc.send_c_store(ds)
    assoc.release()

    # The SCP should report 0xA700 or error status because indexing failed
    assert status is not None
    assert status.Status == 0xA700, f"Expected 0xA700 Out of Resources, got {hex(status.Status)}"

    # Crucial assertion: Blackbox recording MUST STILL EXIST on storage for diagnosis!
    assert media_service.exists_sync(expected_storage_uri), (
        "Blackbox recording must be preserved even when downstream indexing fails"
    )
