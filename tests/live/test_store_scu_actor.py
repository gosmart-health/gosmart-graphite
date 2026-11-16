"""Live MDICOM storage transfer test."""

import pydicom
import pytest

from gosmart_graphite.actor.store_scu_actor import get_store_scu_actor, stop_store_scu_actor
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.service.pynetdicom_service import pynetdicom_service
from tests.utils.config_utils import ConfigUtils

TEST_ASSET_DCM = ConfigUtils.assets_root() / "mr/MR.2.25.10986282349447326157620829373816019196.dcm"
LIVE_MDICOM_HOST = "10.200.0.103"
LIVE_MDICOM_PORT = 11113
LIVE_MDICOM_AET = "MDICOM"


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
