"""Integration tests for local DICOM storage transfers and Neo4j audit records."""

import socket
from typing import List

from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian
from pynetdicom import AE, evt

from gosmart_graphite.actor.store_scu_actor import get_store_scu_actor, stop_store_scu_actor
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.service.dicom_service import dicom_service


def get_free_port() -> int:
    """Find a dynamically available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
