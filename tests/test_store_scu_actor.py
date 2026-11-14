import socket
from pathlib import Path
from typing import List

import pydicom
import pytest
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, JPEG2000Lossless
from pynetdicom import AE, evt

from gosmart_graphite.actor.store_scu_actor import (
    build_store_contexts,
    get_store_scu_actor,
    stop_store_scu_actor,
)
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

TEST_ASSET_DCM = Path("tests/assets/mr/MR.2.25.10986282349447326157620829373816019196.dcm")
LIVE_MDICOM_HOST = "10.200.0.103"
LIVE_MDICOM_PORT = 11113
LIVE_MDICOM_AET = "MDICOM"


def get_free_port() -> int:
    """Find a dynamically available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_build_store_contexts_single_syntax_priority():
    """Verify context builder proposes dedicated single native transfer syntax per context."""
    ds = dicom_service.create_mock_dicom(patient_id="CTX_TEST", modality="MR")
    ds.file_meta.TransferSyntaxUID = JPEG2000Lossless

    contexts = build_store_contexts([ds])
    assert len(contexts) >= 1

    # First context for SOPClassUID must contain ONLY JPEG2000Lossless
    mr_contexts = [c for c in contexts if c.abstract_syntax == ds.SOPClassUID]
    assert len(mr_contexts) >= 1
    assert mr_contexts[0].transfer_syntax == [JPEG2000Lossless]

    # Additional fallback contexts must also be individual single-syntax contexts
    for cx in mr_contexts[1:]:
        assert len(cx.transfer_syntax) == 1
        assert cx.transfer_syntax[0] in (ExplicitVRLittleEndian, ImplicitVRLittleEndian)


def test_store_scu_actor_lifecycle_and_messaging():
    """Test start, stop, ping, and status messages on DicomStoreScuActor."""
    actor = get_store_scu_actor()
    try:
        assert actor.is_alive()

        # Ping
        ping_res = actor.ask({"type": "PING"}, timeout=2.0)
        assert ping_res["status"] == "PONG"
        assert ping_res["pool_size"] >= 1
        assert "workers" in ping_res

        # Status
        status_res = actor.ask({"type": "STATUS"}, timeout=2.0)
        assert "pool_size" in status_res
        assert status_res["total_transfers"] == 0
    finally:
        stop_store_scu_actor()
        assert not actor.is_alive()


def test_store_scu_actor_mock_transfer_and_audit():
    """Test full C-STORE transfer to local mock SCP, verifying TransferStatus record in Neo4j."""
    test_id = "test_store_actor_local"

    # Pre-clean test records as per AGENTS.md
    cleanup_query = "MATCH (t:TransferStatus {test_id: $test_id}) DETACH DELETE t"
    driver = neo4j_client.get_sync_driver()
    with driver.session() as session:
        session.run(cleanup_query, test_id=test_id)

    # 1. Setup local Mock Storage SCP
    received_stores: List[Dataset] = []

    def handle_store(event):
        received_stores.append(event.dataset)
        return 0x0000

    mock_ds = dicom_service.create_mock_dicom(patient_id="ACTOR_LOCAL_P1", modality="MR")
    sop_uid = str(mock_ds.SOPInstanceUID)

    server_port = get_free_port()
    scp_aet = "MOCK_LOCAL_SCP"
    scp_ae = AE(ae_title=scp_aet)
    scp_ae.add_supported_context(mock_ds.SOPClassUID, [ExplicitVRLittleEndian, ImplicitVRLittleEndian])
    server = scp_ae.start_server(
        ("127.0.0.1", server_port),
        block=False,
        evt_handlers=[(evt.EVT_C_STORE, handle_store)],
    )

    actor = get_store_scu_actor()
    try:
        # 2. Dispatch STORE job to actor
        store_res = actor.ask(
            {
                "type": "STORE",
                "destination_aet": scp_aet,
                "host": "127.0.0.1",
                "port": server_port,
                "instances": [mock_ds],
                "test_id": test_id,
                "acted_by": "test_store_runner",
            },
            timeout=10.0,
        )

        assert store_res["success"] is True
        assert store_res["total"] == 1
        assert store_res["completed"] == 1
        assert store_res["failed"] == 0
        assert len(received_stores) == 1
        assert str(received_stores[0].SOPInstanceUID) == sop_uid

        # 3. Verify standalone TransferStatus node in Neo4j
        check_query = """
        MATCH (t:TransferStatus {test_id: $test_id, sop_instance_uid: $sop_uid})
        RETURN t.id AS id, t.sender AS sender, t.recipient AS recipient,
               t.statusCode AS statusCode, t.direction AS direction,
               t.transferTimeMs AS transferTimeMs, t.ExpiresAt AS ExpiresAt,
               count{(t)--()} AS degree
        """
        driver = neo4j_client.get_sync_driver()
        with driver.session() as session:
            records = [r.data() for r in session.run(check_query, test_id=test_id, sop_uid=sop_uid)]

        assert len(records) == 1
        r = records[0]
        assert r["statusCode"] == "0x0000"
        assert r["recipient"] == scp_aet
        assert r["direction"] == "OUTBOUND"
        assert r["degree"] == 0, "TransferStatus must have 0 graph edges"
        assert r["ExpiresAt"] is not None

    finally:
        server.shutdown()
        stop_store_scu_actor()


def test_store_scu_actor_live_mdicom_push():
    """Verify DicomStoreScuActor successfully pushes JPEG 2000 Lossless asset to live MDICOM AE."""
    # Verify MDICOM server availability via C-ECHO; skip test if offline (e.g. CI/CD)
    echo_res = pynetdicom_service.echo_scu(
        host=LIVE_MDICOM_HOST,
        port=LIVE_MDICOM_PORT,
        called_aet=LIVE_MDICOM_AET,
        silent=True,
    )
    if not echo_res.get("success"):
        pytest.skip(
            f"MDICOM server at {LIVE_MDICOM_HOST}:{LIVE_MDICOM_PORT} ({LIVE_MDICOM_AET}) "
            f"unreachable: {echo_res.get('message')}"
        )

    assert TEST_ASSET_DCM.exists(), f"Asset missing: {TEST_ASSET_DCM}"
    ds = pydicom.dcmread(TEST_ASSET_DCM)
    sop_uid = str(ds.SOPInstanceUID)
    test_id = "test_live_mdicom_scu"

    # Pre-clean test records as per AGENTS.md
    cleanup_query = "MATCH (t:TransferStatus {test_id: $test_id}) DETACH DELETE t"
    driver = neo4j_client.get_sync_driver()
    with driver.session() as session:
        session.run(cleanup_query, test_id=test_id)

    actor = get_store_scu_actor()
    try:
        store_res = actor.ask(
            {
                "type": "STORE",
                "destination_aet": LIVE_MDICOM_AET,
                "host": LIVE_MDICOM_HOST,
                "port": LIVE_MDICOM_PORT,
                "instances": [TEST_ASSET_DCM],
                "test_id": test_id,
                "acted_by": "test_live_scu_runner",
            },
            timeout=15.0,
        )

        assert store_res["success"] is True, f"Store failed: {store_res}"
        assert store_res["completed"] == 1
        assert store_res["failed"] == 0
        assert store_res["destination_aet"] == LIVE_MDICOM_AET

        # Verify audit record in Neo4j
        check_query = """
        MATCH (t:TransferStatus {test_id: $test_id, sop_instance_uid: $sop_uid})
        RETURN t.statusCode AS statusCode, t.recipient AS recipient, t.direction AS direction
        """
        driver = neo4j_client.get_sync_driver()
        with driver.session() as session:
            records = [r.data() for r in session.run(check_query, test_id=test_id, sop_uid=sop_uid)]

        assert len(records) == 1
        assert records[0]["statusCode"] == "0x0000"
        assert records[0]["recipient"] == LIVE_MDICOM_AET
        assert records[0]["direction"] == "OUTBOUND"

    finally:
        stop_store_scu_actor()
