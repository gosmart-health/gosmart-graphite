import hashlib
import socket
import uuid
from datetime import datetime, timezone
from typing import Generator

import pydicom
import pytest
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid
from pynetdicom import AE, evt
from pynetdicom.presentation import build_role
from pynetdicom.sop_class import (
    MRImageStorage,
    StudyRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelMove,
)

from gosmart_graphite.actor.dicom_ingestion_actor import get_dicom_actor
from gosmart_graphite.core.config import get_settings
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model.graph import DICOMConfigurationNode
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.media_service import media_service
from gosmart_graphite.service.pynetdicom_service import PyNetDicomService
from tests.utils.config_utils import ConfigUtils

TEST_ASSET_DCM = ConfigUtils.assets_root() / "mr/MR.2.25.10986282349447326157620829373816019196.dcm"


def get_free_port() -> int:
    """Find and return an ephemeral free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _short_patient_id(test_id: str) -> str:
    """Return a deterministic PatientID well within DICOM VR LO (64 chars)."""
    return f"TEST-TS-{hashlib.sha256(test_id.encode()).hexdigest()[:12].upper()}"


def _clone_asset_for_test(test_id: str) -> pydicom.Dataset:
    """Load the compressed MR test asset with deterministic test-scoped identifiers."""
    ds = pydicom.dcmread(TEST_ASSET_DCM)
    sop_uid = generate_uid(entropy_srcs=[test_id, "sop"])
    ds.PatientID = _short_patient_id(test_id)
    ds.StudyInstanceUID = generate_uid(entropy_srcs=[test_id, "study"])
    ds.SeriesInstanceUID = generate_uid(entropy_srcs=[test_id, "series"])
    ds.SOPInstanceUID = sop_uid
    if hasattr(ds, "file_meta") and ds.file_meta:
        ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
    return ds


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
def ts_preservation_env(request: pytest.FixtureRequest) -> Generator[dict, None, None]:
    """Sets up an isolated SCP server instance and clean test state."""
    settings = get_settings()
    test_id = f"test_ts_{request.node.name}"

    # Clean pre-existing test data BEFORE running the test
    graph_service.cleanup_test_data_sync(test_id)

    port = get_free_port()
    service = PyNetDicomService(settings=settings)
    cfg = {
        "id": f"scp_ts_{port}",
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
    assert inst is not None and inst.status == "RUNNING"

    context = {
        "service": service,
        "port": port,
        "ae_title": "GOSMART_GS",
        "test_id": test_id,
        "settings": settings,
    }

    yield context

    # Graceful teardown of server instance
    service.stop_all()


def test_transfer_syntax_c_store_to_c_get_preservation(ts_preservation_env):
    """Verify C-STORE -> Storage -> C-GET retains exact original transfer syntax."""
    port = ts_preservation_env["port"]
    scp_aet = ts_preservation_env["ae_title"]
    test_id = ts_preservation_env["test_id"]

    assert TEST_ASSET_DCM.exists(), f"Asset missing: {TEST_ASSET_DCM}"
    raw_ds = _clone_asset_for_test(test_id)
    orig_ts = raw_ds.file_meta.TransferSyntaxUID
    sop_uid = str(raw_ds.SOPInstanceUID)
    study_uid = str(raw_ds.StudyInstanceUID)

    storage_root = ts_preservation_env["settings"].STORAGE_ROOT_URI.rstrip("/")
    storage_uri = f"{storage_root}/{sop_uid}.dcm"
    if media_service.exists_sync(storage_uri):
        media_service.delete_sync(storage_uri)

    # 1. Client pushes dataset via C-STORE
    client_ae = AE(ae_title="TEST_STORE_SCU")
    client_ae.add_requested_context(raw_ds.SOPClassUID, [orig_ts])
    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established
    status = assoc.send_c_store(raw_ds)
    assoc.release()
    assert status is not None and status.Status == 0x0000

    actor = get_dicom_actor()
    assert actor is not None and actor.ask({"type": "PING"}, timeout=10.0)["status"] == "PONG"

    # 2. Verify stored Part-10 file preserves original TransferSyntaxUID
    assert media_service.exists_sync(storage_uri)
    stored_ds = media_service.read_dicom_sync(storage_uri)
    assert stored_ds.file_meta.TransferSyntaxUID == orig_ts

    # 3. Verify Neo4j :Instance node recorded original transfer_syntax_uid
    driver = neo4j_client.get_sync_driver()
    with driver.session() as session:
        result = session.run(
            "MATCH (i:Instance {sop_instance_uid: $sop_uid}) RETURN i.transfer_syntax_uid AS ts_uid",
            sop_uid=sop_uid,
        )
        rec = result.single()
        assert rec is not None
        assert rec["ts_uid"] == str(orig_ts)
    _tag_hierarchy_test_id(sop_uid, test_id)

    # 4. Client retrieves dataset via C-GET
    received_contexts = []

    def handle_get_store(event):
        received_contexts.append((event.context.transfer_syntax, event.file_meta.TransferSyntaxUID))
        return 0x0000

    get_ae = AE(ae_title="TEST_GET_SCU")
    get_ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
    get_ae.add_requested_context(MRImageStorage, [orig_ts])
    role = build_role(MRImageStorage, scp_role=True)

    get_assoc = get_ae.associate(
        "127.0.0.1",
        port,
        ae_title=scp_aet,
        ext_neg=[role],
        evt_handlers=[(evt.EVT_C_STORE, handle_get_store)],
    )
    assert get_assoc.is_established

    get_ds = Dataset()
    get_ds.QueryRetrieveLevel = "STUDY"
    get_ds.StudyInstanceUID = study_uid

    responses = list(get_assoc.send_c_get(get_ds, StudyRootQueryRetrieveInformationModelGet))
    get_assoc.release()

    final_status, _ = responses[-1]
    assert final_status.Status == 0x0000, f"Expected 0x0000 Success, got {hex(final_status.Status)}"
    assert len(received_contexts) >= 1
    assert received_contexts[0][0] == orig_ts
    assert received_contexts[0][1] == orig_ts


def test_transfer_syntax_c_store_to_c_move_preservation(ts_preservation_env):
    """Verify C-STORE -> Storage -> C-MOVE sub-operation delivers exact original transfer syntax."""
    port = ts_preservation_env["port"]
    scp_aet = ts_preservation_env["ae_title"]
    test_id = ts_preservation_env["test_id"]

    assert TEST_ASSET_DCM.exists(), f"Asset missing: {TEST_ASSET_DCM}"
    raw_ds = _clone_asset_for_test(test_id)
    orig_ts = raw_ds.file_meta.TransferSyntaxUID
    sop_uid = str(raw_ds.SOPInstanceUID)
    study_uid = str(raw_ds.StudyInstanceUID)

    storage_root = ts_preservation_env["settings"].STORAGE_ROOT_URI.rstrip("/")
    storage_uri = f"{storage_root}/{sop_uid}.dcm"
    if media_service.exists_sync(storage_uri):
        media_service.delete_sync(storage_uri)

    # 1. Ingest via C-STORE
    client_ae = AE(ae_title="TEST_STORE_SCU")
    client_ae.add_requested_context(raw_ds.SOPClassUID, [orig_ts])
    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established
    status = assoc.send_c_store(raw_ds)
    assoc.release()
    assert status is not None and status.Status == 0x0000

    actor = get_dicom_actor()
    assert actor is not None and actor.ask({"type": "PING"}, timeout=10.0)["status"] == "PONG"

    _tag_hierarchy_test_id(sop_uid, test_id)

    # 2. Setup Destination Storage SCP that supports the original transfer syntax
    received_move_ts = []

    def handle_move_store(event):
        received_move_ts.append((event.context.transfer_syntax, event.file_meta.TransferSyntaxUID))
        return 0x0000

    dest_aet = f"DST_{uuid.uuid4().hex[:8].upper()}"
    dest_ae = AE(ae_title=dest_aet)
    dest_ae.add_supported_context(MRImageStorage, [orig_ts])
    dest_server = dest_ae.start_server(
        ("127.0.0.1", 0),
        block=False,
        evt_handlers=[(evt.EVT_C_STORE, handle_move_store)],
    )
    dest_port = dest_server.server_address[1]

    driver = neo4j_client.get_sync_driver()
    try:
        # Register Destination AE in Neo4j
        dest_cfg = DICOMConfigurationNode(
            ae_title=dest_aet,
            host="127.0.0.1",
            port=dest_port,
            roles=["SCP"],
            test_id=test_id,
            CreatedAt=datetime.now(timezone.utc),
        )
        dest_props = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dest_cfg.model_dump().items()}
        with driver.session() as session:
            session.run("CREATE (c:DICOMConfiguration) SET c = $props", props=dest_props)

        # 3. Client initiates C-MOVE
        move_ae = AE(ae_title="TEST_MOVE_SCU")
        move_ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
        move_assoc = move_ae.associate("127.0.0.1", port, ae_title=scp_aet)
        assert move_assoc.is_established

        move_ds = Dataset()
        move_ds.QueryRetrieveLevel = "STUDY"
        move_ds.StudyInstanceUID = study_uid

        responses = list(move_assoc.send_c_move(move_ds, dest_aet, StudyRootQueryRetrieveInformationModelMove))
        move_assoc.release()

        final_status, _ = responses[-1]
        assert final_status.Status == 0x0000, f"Expected 0x0000 Success, got {hex(final_status.Status)}"

        # 4. Verify Destination SCP received the image with original transfer syntax
        assert len(received_move_ts) >= 1
        assert received_move_ts[0][0] == orig_ts
        assert received_move_ts[0][1] == orig_ts
    finally:
        dest_server.shutdown()


def test_compressed_syntax_transcoding_protection(ts_preservation_env):
    """Verify pynetdicom rejects converting compressed data if peer only accepts uncompressed syntax."""
    port = ts_preservation_env["port"]
    scp_aet = ts_preservation_env["ae_title"]
    test_id = ts_preservation_env["test_id"]

    raw_ds = _clone_asset_for_test(test_id)
    orig_ts = raw_ds.file_meta.TransferSyntaxUID
    assert orig_ts.is_compressed, "Test asset must be a compressed transfer syntax"
    sop_uid = str(raw_ds.SOPInstanceUID)
    study_uid = str(raw_ds.StudyInstanceUID)

    storage_root = ts_preservation_env["settings"].STORAGE_ROOT_URI.rstrip("/")
    storage_uri = f"{storage_root}/{sop_uid}.dcm"
    if media_service.exists_sync(storage_uri):
        media_service.delete_sync(storage_uri)

    # Ingest compressed asset
    client_ae = AE(ae_title="TEST_STORE_SCU")
    client_ae.add_requested_context(raw_ds.SOPClassUID, [orig_ts])
    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established
    status = assoc.send_c_store(raw_ds)
    assoc.release()
    assert status is not None and status.Status == 0x0000

    actor = get_dicom_actor()
    assert actor is not None and actor.ask({"type": "PING"}, timeout=10.0)["status"] == "PONG"

    _tag_hierarchy_test_id(sop_uid, test_id)

    # Destination AE ONLY accepts uncompressed Explicit VR Little Endian
    received_move = []

    def handle_move_store(event):
        received_move.append(event.dataset)
        return 0x0000

    dest_aet = f"DST_{uuid.uuid4().hex[:8].upper()}"
    dest_ae = AE(ae_title=dest_aet)
    dest_ae.add_supported_context(MRImageStorage, [ExplicitVRLittleEndian])
    dest_server = dest_ae.start_server(
        ("127.0.0.1", 0),
        block=False,
        evt_handlers=[(evt.EVT_C_STORE, handle_move_store)],
    )
    dest_port = dest_server.server_address[1]

    driver = neo4j_client.get_sync_driver()
    try:
        dest_cfg = DICOMConfigurationNode(
            ae_title=dest_aet,
            host="127.0.0.1",
            port=dest_port,
            roles=["SCP"],
            test_id=test_id,
            CreatedAt=datetime.now(timezone.utc),
        )
        dest_props = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dest_cfg.model_dump().items()}
        with driver.session() as session:
            session.run("CREATE (c:DICOMConfiguration) SET c = $props", props=dest_props)

        move_ae = AE(ae_title="TEST_MOVE_SCU")
        move_ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
        move_assoc = move_ae.associate("127.0.0.1", port, ae_title=scp_aet)
        assert move_assoc.is_established

        move_ds = Dataset()
        move_ds.QueryRetrieveLevel = "STUDY"
        move_ds.StudyInstanceUID = study_uid

        responses = list(move_assoc.send_c_move(move_ds, dest_aet, StudyRootQueryRetrieveInformationModelMove))
        move_assoc.release()

        # The C-MOVE operation must report sub-operation failure (0xA702 or warning 0xB000)
        final_status, _ = responses[-1]
        assert final_status.Status in (0xA702, 0xB000)
        # Verify no corrupt/coerced dataset was delivered to destination
        assert len(received_move) == 0
    finally:
        dest_server.shutdown()


def test_uncompressed_transfer_syntax_preservation(ts_preservation_env):
    """Verify uncompressed Explicit VR Little Endian is stored and sent without alteration."""
    port = ts_preservation_env["port"]
    scp_aet = ts_preservation_env["ae_title"]
    test_id = ts_preservation_env["test_id"]

    mock_ds = dicom_service.create_mock_dicom(
        patient_id=_short_patient_id(test_id),
        patient_name="UNCOMPRESSED^TEST",
        modality="OT",
        study_description="Uncompressed TS Test",
    )
    mock_ds.SOPClassUID = SecondaryCaptureImageStorage
    mock_ds.StudyInstanceUID = generate_uid(entropy_srcs=[test_id, "study"])
    mock_ds.SeriesInstanceUID = generate_uid(entropy_srcs=[test_id, "series"])
    sop_uid = generate_uid(entropy_srcs=[test_id, "sop"])
    mock_ds.SOPInstanceUID = sop_uid
    mock_ds.file_meta.MediaStorageSOPInstanceUID = sop_uid
    orig_ts = mock_ds.file_meta.TransferSyntaxUID
    assert orig_ts == ExplicitVRLittleEndian
    study_uid = str(mock_ds.StudyInstanceUID)

    storage_root = ts_preservation_env["settings"].STORAGE_ROOT_URI.rstrip("/")
    storage_uri = f"{storage_root}/{sop_uid}.dcm"
    if media_service.exists_sync(storage_uri):
        media_service.delete_sync(storage_uri)

    # Ingest mock uncompressed dataset via C-STORE
    client_ae = AE(ae_title="TEST_STORE_SCU")
    client_ae.add_requested_context(SecondaryCaptureImageStorage, [orig_ts])
    assoc = client_ae.associate("127.0.0.1", port, ae_title=scp_aet)
    assert assoc.is_established
    status = assoc.send_c_store(mock_ds)
    assoc.release()
    assert status is not None and status.Status == 0x0000

    actor = get_dicom_actor()
    assert actor is not None and actor.ask({"type": "PING"}, timeout=10.0)["status"] == "PONG"

    _tag_hierarchy_test_id(sop_uid, test_id)

    # C-GET verification
    received_get_ts = []

    def handle_get_store(event):
        received_get_ts.append((event.context.transfer_syntax, event.file_meta.TransferSyntaxUID))
        return 0x0000

    get_ae = AE(ae_title="TEST_GET_SCU")
    get_ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
    get_ae.add_requested_context(SecondaryCaptureImageStorage, [orig_ts])
    role = build_role(SecondaryCaptureImageStorage, scp_role=True)

    get_assoc = get_ae.associate(
        "127.0.0.1",
        port,
        ae_title=scp_aet,
        ext_neg=[role],
        evt_handlers=[(evt.EVT_C_STORE, handle_get_store)],
    )
    assert get_assoc.is_established

    get_ds = Dataset()
    get_ds.QueryRetrieveLevel = "STUDY"
    get_ds.StudyInstanceUID = study_uid

    responses = list(get_assoc.send_c_get(get_ds, StudyRootQueryRetrieveInformationModelGet))
    get_assoc.release()

    final_status, _ = responses[-1]
    assert final_status.Status == 0x0000
    assert len(received_get_ts) >= 1
    assert received_get_ts[0][0] == ExplicitVRLittleEndian
    assert received_get_ts[0][1] == ExplicitVRLittleEndian
