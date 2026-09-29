import socket
from pathlib import Path
from unittest.mock import patch

import pydicom
import pytest
from pynetdicom import AE

from core.config import get_settings
from service.media_service import media_service
from service.pynetdicom_service import PyNetDicomService

TEST_ASSET_DCM = Path("tests/assets/mr/MR.2.25.10986282349447326157620829373816019196.dcm")


def get_free_port() -> int:
    """Find a dynamically available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

    result = service.echo_scu(host="127.0.0.1", port=port, called_aet="GOSMART_GS")
    assert result["success"] is True
    assert result["status_code"] == "0x0"


def test_dicom_c_store_blackbox_persistence(dicom_server):
    """Test C-STORE SCU -> SCP transfer preserves raw blackbox file and indexes graph."""
    service, port = dicom_server
    settings = get_settings()

    assert TEST_ASSET_DCM.exists(), f"Asset missing: {TEST_ASSET_DCM}"
    ds = pydicom.dcmread(TEST_ASSET_DCM)
    sop_uid = str(ds.SOPInstanceUID)
    expected_storage_uri = f"{settings.STORAGE_ROOT_URI.rstrip('/')}/{sop_uid}.dcm"

    # Pre-clean storage if exists
    if media_service.exists_sync(expected_storage_uri):
        media_service.delete_sync(expected_storage_uri)

    # Send C-STORE using an SCU Application Entity
    client_ae = AE(ae_title="TEST_SCU")
    client_ae.add_requested_context(ds.SOPClassUID, ds.file_meta.TransferSyntaxUID)

    assoc = client_ae.associate("127.0.0.1", port, ae_title="GOSMART_GS")
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
    from db.neo4j import neo4j_client

    try:
        driver = neo4j_client.get_sync_driver()
        with driver.session() as session:
            result = session.run(
                "MATCH (i:Instance {sop_instance_uid: $sop_uid}) "
                "RETURN i.Source AS Source, i.SourceIdentity AS SourceIdentity, "
                "i.Destination AS Destination, i.DestinationIdentity AS DestinationIdentity",
                sop_uid=sop_uid,
            )
            records = [record.data() for record in result]
            if records:
                inst = records[0]
                assert inst["Source"] == "dimse:127.0.0.1"
                assert inst["SourceIdentity"] == "ae:TEST_SCU"
                assert inst["Destination"].startswith("dimse:")
                assert inst["DestinationIdentity"] == "ae:GOSMART_GS"
    except Exception:
        pass


def test_dicom_c_store_blackbox_preservation_on_downstream_failure(dicom_server):
    """Verify raw DICOM file is preserved on storage even if downstream graph indexing fails."""
    service, port = dicom_server
    settings = get_settings()

    ds = pydicom.dcmread(TEST_ASSET_DCM)
    # Generate a unique SOP instance for this failure test
    ds.SOPInstanceUID = "9.9.9.9.9999.failure.test"
    if hasattr(ds, "file_meta") and ds.file_meta:
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID

    sop_uid = str(ds.SOPInstanceUID)
    expected_storage_uri = f"{settings.STORAGE_ROOT_URI.rstrip('/')}/{sop_uid}.dcm"

    # Pre-clean storage
    if media_service.exists_sync(expected_storage_uri):
        media_service.delete_sync(expected_storage_uri)

    client_ae = AE(ae_title="TEST_SCU")
    client_ae.add_requested_context(ds.SOPClassUID, ds.file_meta.TransferSyntaxUID)

    assoc = client_ae.associate("127.0.0.1", port, ae_title="GOSMART_GS")
    assert assoc.is_established

    # Simulate downstream Neo4j failure
    with patch(
        "service.graph_service.graph_service.save_hierarchy_sync",
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
