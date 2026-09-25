import pytest
from actor.dicom_actor import DicomIngestionActor


def test_actor_lifecycle_and_messaging():
    actor = DicomIngestionActor.start()
    try:
        assert actor.is_alive()

        # Test PING
        ping_res = actor.ask({"type": "PING"}, timeout=2.0)
        assert ping_res["status"] == "PONG"
        assert ping_res["processed_count"] == 0

        # Test Unknown message handling
        unknown_res = actor.ask({"type": "UNKNOWN_ACTION"}, timeout=2.0)
        assert "error" in unknown_res
    finally:
        actor.stop()
        assert not actor.is_alive()
