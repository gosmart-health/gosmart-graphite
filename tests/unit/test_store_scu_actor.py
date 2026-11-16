import socket

from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, JPEG2000Lossless

from gosmart_graphite.actor.store_scu_actor import (
    build_store_contexts,
    get_store_scu_actor,
    stop_store_scu_actor,
)
from gosmart_graphite.service.dicom_service import dicom_service


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
