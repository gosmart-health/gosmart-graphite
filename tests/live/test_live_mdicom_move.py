import socket
from datetime import datetime, timezone

import pydicom
import pytest
from pydicom.dataset import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelMove

from gosmart_graphite.core.config import get_settings
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model.dicom_config import DICOMConfigurationNode
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.media_service import media_service
from gosmart_graphite.service.pynetdicom_service import PyNetDicomService, pynetdicom_service
from tests.utils.config_utils import ConfigUtils

TEST_ASSET_DCM = ConfigUtils.assets_root() / "mr/MR.2.25.10986282349447326157620829373816019196.dcm"
LIVE_MDICOM_HOST = "10.200.0.103"
LIVE_MDICOM_PORT = 11113
LIVE_MDICOM_AET = "MDICOM"


def get_free_port() -> int:
    """Find a dynamically available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_live_mdicom_c_move_push():
    """Verify live C-MOVE retrieve sub-operation pushes cleanly to MDICOM at 10.200.0.103:11113.

    This test exercises the live, external Micro DICOM server to ensure that presentation
    context negotiation and C-STORE sub-operations complete with 0x0000 (Success) and zero failures.
    If MDICOM is not reachable (e.g., in CI/CD), the test gracefully skips.
    """
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
    settings = get_settings()
    test_id = "test_live_mdicom"

    # Clean pre-existing test data before running test (keep post-run data intact for manual inspection)
    driver = neo4j_client.get_sync_driver()
    with driver.session() as session:
        session.run("MATCH (n {test_id: $test_id}) DETACH DELETE n", test_id=test_id)

    # 1. Seed test asset
    assert TEST_ASSET_DCM.exists(), f"Asset missing: {TEST_ASSET_DCM}"
    ds = pydicom.dcmread(TEST_ASSET_DCM)
    sop_uid = str(ds.SOPInstanceUID)
    study_uid = str(ds.StudyInstanceUID)

    storage_uri = f"{settings.STORAGE_ROOT_URI.rstrip('/')}/{sop_uid}.dcm"
    media_service.write_dicom_sync(storage_uri, ds)

    hierarchy = dicom_service.parse_file(TEST_ASSET_DCM)
    hierarchy.test_id = test_id
    hierarchy.acted_by = "test_live_mdicom_runner"
    hierarchy.instance.storage_uri = storage_uri
    graph_service.save_hierarchy_sync(hierarchy)

    # 2. Register live destination AE in Neo4j
    dest_cfg = DICOMConfigurationNode(
        ae_title=LIVE_MDICOM_AET,
        host=LIVE_MDICOM_HOST,
        port=LIVE_MDICOM_PORT,
        roles=["SCP"],
        test_id=test_id,
        CreatedAt=datetime.now(timezone.utc),
    )
    dest_cfg_params = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dest_cfg.model_dump().items()}
    with driver.session() as session:
        session.run(
            "MATCH (c:DICOMConfiguration {ae_title: $aet, test_id: $tid}) DETACH DELETE c",
            aet=LIVE_MDICOM_AET,
            tid=test_id,
        )
        session.run("CREATE (c:DICOMConfiguration) SET c = $props", props=dest_cfg_params)

    # 3. Start local SCP on dynamic free port
    server_port = get_free_port()
    scp_service = PyNetDicomService(settings=settings)
    scp_cfg = {
        "id": f"scp_live_test_{server_port}",
        "ae_title": "GOSMART_GS",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": server_port,
        "is_local": True,
        "is_promiscuous": False,
        "dicom_services": ["*"],
        "transfer_syntaxes": ["*"],
    }
    inst = scp_service.start_scp_for_config(scp_cfg)
    assert inst is not None
    assert inst.status == "RUNNING"

    try:
        # 4. SCU connects to local SCP and initiates C-MOVE to LIVE_MDICOM_AET
        client_ae = AE(ae_title="TEST_MOVE_SCU")
        client_ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)

        assoc = client_ae.associate("127.0.0.1", server_port, ae_title="GOSMART_GS")
        assert assoc.is_established, "Failed to establish association with local Move SCP"

        move_ds = Dataset()
        move_ds.QueryRetrieveLevel = "STUDY"
        move_ds.StudyInstanceUID = study_uid

        responses = list(assoc.send_c_move(move_ds, LIVE_MDICOM_AET, StudyRootQueryRetrieveInformationModelMove))
        assoc.release()

        assert len(responses) >= 1, "No C-MOVE responses received"
        final_status, _ = responses[-1]
        assert final_status.Status == 0x0000, f"C-MOVE failed with status {hex(final_status.Status)}"
        completed = getattr(final_status, "NumberOfCompletedSuboperations", None)
        failed = getattr(final_status, "NumberOfFailedSuboperations", 0)

        assert failed == 0, f"Sub-operations failed: {failed}"
        assert completed is not None and completed > 0, f"No sub-operations completed: {completed}"

    finally:
        scp_service.stop_all()
