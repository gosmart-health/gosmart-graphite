import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pydicom
import pytest
from pydicom.dataset import Dataset
from pynetdicom import AE, evt
from pynetdicom.presentation import build_role
from pynetdicom.sop_class import (
    MRImageStorage,
    PatientRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelMove,
)

from core.config import get_settings
from db.neo4j import neo4j_client
from model.graph import DICOMConfigurationNode
from service.dicom_service import dicom_service
from service.graph_service import graph_service
from service.media_service import media_service
from service.pynetdicom_service import PyNetDicomService

TEST_ASSET_DCM = Path("tests/assets/mr/MR.2.25.10986282349447326157620829373816019196.dcm")


def get_free_port() -> int:
    """Find a dynamically available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def dicom_qr_env():
    """Sets up a running DICOM SCP server and seeded test data in Neo4j and storage."""
    settings = get_settings()
    test_id = f"test_qr_{uuid.uuid4().hex[:8]}"

    # Clean pre-existing test data BEFORE running the test
    driver = neo4j_client.get_sync_driver()
    with driver.session() as session:
        session.run("MATCH (n {test_id: $test_id}) DETACH DELETE n", test_id=test_id)

    # Seed test asset
    assert TEST_ASSET_DCM.exists(), f"Asset missing: {TEST_ASSET_DCM}"
    ds = pydicom.dcmread(TEST_ASSET_DCM)
    sop_uid = str(ds.SOPInstanceUID)
    study_uid = str(ds.StudyInstanceUID)
    series_uid = str(ds.SeriesInstanceUID)
    patient_id = str(ds.PatientID)
    patient_name = str(getattr(ds, "PatientName", "UNKNOWN"))

    storage_uri = f"{settings.STORAGE_ROOT_URI.rstrip('/')}/{sop_uid}.dcm"
    media_service.write_dicom_sync(storage_uri, ds)

    # Parse and register hierarchy into Neo4j
    hierarchy = dicom_service.parse_file(TEST_ASSET_DCM)
    hierarchy.test_id = test_id
    hierarchy.acted_by = "test_qr_runner"
    hierarchy.instance.storage_uri = storage_uri
    graph_service.save_hierarchy_sync(hierarchy)

    # Start SCP on dynamic free port
    port = get_free_port()
    service = PyNetDicomService(settings=settings)
    cfg = {
        "id": f"scp_qr_{port}",
        "ae_title": "GOSMART_GS",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": port,
        "is_local": True,
        "is_promiscuous": False,
        "dicom_services": ["*"],
        "transfer_syntaxes": ["*"],
    }
    inst = service.start_scp_for_config(cfg)
    assert inst is not None
    assert inst.status == "RUNNING"

    context = {
        "service": service,
        "port": port,
        "ae_title": "GOSMART_GS",
        "test_id": test_id,
        "patient_id": patient_id,
        "patient_name": patient_name,
        "study_uid": study_uid,
        "series_uid": series_uid,
        "sop_uid": sop_uid,
        "ds": ds,
        "storage_uri": storage_uri,
    }

    yield context

    # Graceful teardown
    service.stop_all()


def test_dimse_c_find_patient_level(dicom_qr_env):
    """Test C-FIND at PATIENT level returning matched patient record."""
    port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    patient_id = dicom_qr_env["patient_id"]

    client_ae = AE(ae_title="TEST_FIND_SCU")
    client_ae.add_requested_context(PatientRootQueryRetrieveInformationModelFind)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established

    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = "PATIENT"
    query_ds.PatientID = patient_id
    query_ds.PatientName = ""

    responses = list(assoc.send_c_find(query_ds, PatientRootQueryRetrieveInformationModelFind))
    assoc.release()

    # responses contain (status, dataset) tuples
    matched_datasets = [ds for status, ds in responses if ds is not None]
    assert len(matched_datasets) >= 1
    found = False
    for ds in matched_datasets:
        if ds.PatientID == patient_id:
            found = True
            assert ds.QueryRetrieveLevel == "PATIENT"
            assert hasattr(ds, "PatientName")
    assert found, f"PatientID {patient_id} not found in C-FIND responses"


def test_dimse_c_find_study_level(dicom_qr_env):
    """Test C-FIND at STUDY level returning matched study record."""
    port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]
    patient_id = dicom_qr_env["patient_id"]

    client_ae = AE(ae_title="TEST_FIND_SCU")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established

    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = "STUDY"
    query_ds.StudyInstanceUID = study_uid
    query_ds.PatientID = patient_id

    responses = list(assoc.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind))
    assoc.release()

    matched_datasets = [ds for status, ds in responses if ds is not None]
    assert len(matched_datasets) >= 1
    assert any(ds.StudyInstanceUID == study_uid for ds in matched_datasets)


def test_dimse_c_find_study_level_empty_return_keys(dicom_qr_env):
    """Test C-FIND at STUDY level with empty return keys as sent by MicroDICOM."""
    port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]
    patient_id = dicom_qr_env["patient_id"]

    client_ae = AE(ae_title="MICRODICOM")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established

    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = "STUDY"
    query_ds.StudyInstanceUID = ""
    query_ds.PatientID = ""
    query_ds.PatientName = ""
    query_ds.StudyDate = ""
    query_ds.AccessionNumber = ""

    responses = list(assoc.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind))
    assoc.release()

    matched_datasets = [ds for status, ds in responses if ds is not None]
    assert len(matched_datasets) >= 1
    target = next((ds for ds in matched_datasets if ds.StudyInstanceUID == study_uid), None)
    assert target is not None, f"Study {study_uid} not returned when query attributes are empty return keys"
    assert target.PatientID == patient_id
    assert hasattr(target, "PatientName")
    assert hasattr(target, "PatientBirthDate")
    assert hasattr(target, "PatientSex")


def test_dimse_c_find_study_level_wildcard_matching(dicom_qr_env):
    """Test C-FIND at STUDY level with wildcard '*' parameters."""
    port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]

    client_ae = AE(ae_title="TEST_FIND_SCU")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established

    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = "STUDY"
    query_ds.StudyInstanceUID = "*"
    query_ds.PatientID = "*"
    query_ds.PatientName = "*"
    query_ds.StudyDate = "*"

    responses = list(assoc.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind))
    assoc.release()

    matched_datasets = [ds for status, ds in responses if ds is not None]
    assert len(matched_datasets) >= 1
    assert any(ds.StudyInstanceUID == study_uid for ds in matched_datasets)


def test_dimse_c_find_series_level_empty_return_keys(dicom_qr_env):
    """Test C-FIND at SERIES level with empty return keys."""
    port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]
    series_uid = dicom_qr_env["series_uid"]

    client_ae = AE(ae_title="TEST_FIND_SCU")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established

    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = "SERIES"
    query_ds.StudyInstanceUID = study_uid
    query_ds.SeriesInstanceUID = ""
    query_ds.Modality = ""

    responses = list(assoc.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind))
    assoc.release()

    matched_datasets = [ds for status, ds in responses if ds is not None]
    assert len(matched_datasets) >= 1
    assert any(ds.SeriesInstanceUID == series_uid for ds in matched_datasets)


def test_dimse_c_find_series_level(dicom_qr_env):
    """Test C-FIND at SERIES level returning matched series record."""
    port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]
    series_uid = dicom_qr_env["series_uid"]

    client_ae = AE(ae_title="TEST_FIND_SCU")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established

    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = "SERIES"
    query_ds.StudyInstanceUID = study_uid
    query_ds.SeriesInstanceUID = series_uid

    responses = list(assoc.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind))
    assoc.release()

    matched_datasets = [ds for status, ds in responses if ds is not None]
    assert len(matched_datasets) >= 1
    assert any(ds.SeriesInstanceUID == series_uid for ds in matched_datasets)


def test_dimse_c_find_image_level(dicom_qr_env):
    """Test C-FIND at IMAGE level returning matched instance record."""
    port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]
    series_uid = dicom_qr_env["series_uid"]
    sop_uid = dicom_qr_env["sop_uid"]

    client_ae = AE(ae_title="TEST_FIND_SCU")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)

    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established

    query_ds = Dataset()
    query_ds.QueryRetrieveLevel = "IMAGE"
    query_ds.StudyInstanceUID = study_uid
    query_ds.SeriesInstanceUID = series_uid
    query_ds.SOPInstanceUID = sop_uid

    responses = list(assoc.send_c_find(query_ds, StudyRootQueryRetrieveInformationModelFind))
    assoc.release()

    matched_datasets = [ds for status, ds in responses if ds is not None]
    assert len(matched_datasets) >= 1
    assert any(ds.SOPInstanceUID == sop_uid for ds in matched_datasets)


def test_dimse_c_move_success(dicom_qr_env):
    """Test C-MOVE retrieve sub-operations to a destination Storage SCP."""
    server_port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]
    sop_uid = dicom_qr_env["sop_uid"]
    test_id = dicom_qr_env["test_id"]

    # 1. Setup ephemeral Destination Storage SCP to receive sub-operations
    received_datasets = []

    def handle_store(event):
        received_datasets.append(event.dataset)
        return 0x0000

    dest_aet = f"DST_{uuid.uuid4().hex[:8].upper()}"
    dest_ae = AE(ae_title=dest_aet)
    ts = dicom_qr_env["ds"].file_meta.TransferSyntaxUID
    dest_ae.add_supported_context(MRImageStorage, [ts])
    dest_server = dest_ae.start_server(
        ("127.0.0.1", 0),
        block=False,
        evt_handlers=[(evt.EVT_C_STORE, handle_store)],
    )
    dest_port = dest_server.server_address[1]

    try:
        # 2. Register Destination AE in Neo4j (:DICOMConfiguration)
        dest_cfg = DICOMConfigurationNode(
            ae_title=dest_aet,
            host="127.0.0.1",
            port=dest_port,
            roles=["SCP"],
            test_id=test_id,
            CreatedAt=datetime.now(timezone.utc),
        )
        dest_cfg_params = {
            k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dest_cfg.model_dump().items()
        }
        driver = neo4j_client.get_sync_driver()
        with driver.session() as session:
            session.run("MATCH (c:DICOMConfiguration {ae_title: $aet}) DETACH DELETE c", aet=dest_aet)
            session.run(
                "CREATE (c:DICOMConfiguration) SET c = $props",
                props=dest_cfg_params,
            )

        # 3. Client SCU initiates C-MOVE
        client_ae = AE(ae_title="TEST_MOVE_SCU")
        client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)

        assoc = client_ae.associate("127.0.0.1", server_port, ae_title=scp_aet)
        assert assoc.is_established

        move_ds = Dataset()
        move_ds.QueryRetrieveLevel = "STUDY"
        move_ds.StudyInstanceUID = study_uid

        responses = list(assoc.send_c_move(move_ds, dest_aet, StudyRootQueryRetrieveInformationModelMove))
        assoc.release()

        # Final response status should be 0x0000 (Success)
        final_status, _ = responses[-1]
        assert final_status.Status == 0x0000, f"Expected 0x0000 Success, got {hex(final_status.Status)}"

        # 4. Verify Destination SCP received the C-STORE sub-operation
        assert len(received_datasets) >= 1
        assert any(str(ds.SOPInstanceUID) == sop_uid for ds in received_datasets)

    finally:
        dest_server.shutdown()


def test_dimse_c_move_haz_009_local_loop_rejection(dicom_qr_env):
    """Test HAZ-009 loop prevention: C-MOVE destination cannot be local SCP AE title."""
    server_port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]

    client_ae = AE(ae_title="TEST_MOVE_SCU")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)

    assoc = client_ae.associate("127.0.0.1", server_port, ae_title=scp_aet)
    assert assoc.is_established

    move_ds = Dataset()
    move_ds.QueryRetrieveLevel = "STUDY"
    move_ds.StudyInstanceUID = study_uid

    # Attempt to C-MOVE to the same AE Title as the SCP (Loop attempt)
    responses = list(assoc.send_c_move(move_ds, scp_aet, StudyRootQueryRetrieveInformationModelMove))
    assoc.release()

    final_status, _ = responses[-1]
    # pynetdicom returns 0xA801 (Move Destination Unknown) when destination is rejected
    assert final_status.Status != 0x0000
    assert final_status.Status == 0xA801


def test_dimse_c_move_unknown_destination_rejection(dicom_qr_env):
    """Test C-MOVE rejection when destination AE is not configured in Neo4j."""
    server_port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]

    client_ae = AE(ae_title="TEST_MOVE_SCU")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)

    assoc = client_ae.associate("127.0.0.1", server_port, ae_title=scp_aet)
    assert assoc.is_established

    move_ds = Dataset()
    move_ds.QueryRetrieveLevel = "STUDY"
    move_ds.StudyInstanceUID = study_uid

    responses = list(assoc.send_c_move(move_ds, "UNKNOWN_DEST_AE", StudyRootQueryRetrieveInformationModelMove))
    assoc.release()

    final_status, _ = responses[-1]
    assert final_status.Status == 0xA801


def test_dimse_c_get_success(dicom_qr_env):
    """Test C-GET retrieve streaming instances across the same association via reverse role."""
    server_port = dicom_qr_env["port"]
    scp_aet = dicom_qr_env["ae_title"]
    study_uid = dicom_qr_env["study_uid"]
    sop_uid = dicom_qr_env["sop_uid"]

    received_datasets = []

    def handle_store(event):
        received_datasets.append(event.dataset)
        return 0x0000

    client_ae = AE(ae_title="TEST_GET_SCU")
    client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
    ts = dicom_qr_env["ds"].file_meta.TransferSyntaxUID
    client_ae.add_requested_context(MRImageStorage, [ts])

    # Request Reverse Role: Client SCU acts as SCP for Storage during C-GET
    role = build_role(MRImageStorage, scp_role=True)

    assoc = client_ae.associate(
        "127.0.0.1",
        server_port,
        ae_title=scp_aet,
        ext_neg=[role],
        evt_handlers=[(evt.EVT_C_STORE, handle_store)],
    )
    assert assoc.is_established

    get_ds = Dataset()
    get_ds.QueryRetrieveLevel = "STUDY"
    get_ds.StudyInstanceUID = study_uid

    responses = list(assoc.send_c_get(get_ds, StudyRootQueryRetrieveInformationModelGet))
    assoc.release()

    final_status, _ = responses[-1]
    assert final_status.Status == 0x0000, f"Expected 0x0000 Success, got {hex(final_status.Status)}"
    assert len(received_datasets) >= 1
    assert any(str(ds.SOPInstanceUID) == sop_uid for ds in received_datasets)
