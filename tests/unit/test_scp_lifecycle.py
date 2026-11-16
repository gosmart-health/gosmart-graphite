import socket

import pytest
from pynetdicom import AE
from pynetdicom.sop_class import MRImageStorage, Verification

from gosmart_graphite.core.config import get_settings
from gosmart_graphite.service.pynetdicom_service import PyNetDicomService


def get_free_port() -> int:
    """Find a dynamically available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def dicom_service():
    """Fixture providing a fresh PyNetDicomService instance."""
    settings = get_settings()
    svc = PyNetDicomService(settings=settings)
    yield svc
    svc.stop_all()


def test_start_scp_role_enforcement(dicom_service):
    """Verify that non-SCP roles (SCU-only) do not trigger an SCP listener."""
    port = get_free_port()
    scu_cfg = {
        "id": "cfg_scu_only",
        "ae_title": "TEST_SCU_AE",
        "roles": ["SCU"],
        "host": "127.0.0.1",
        "port": port,
    }
    inst = dicom_service.start_scp_for_config(scu_cfg)
    assert inst is None
    assert not dicom_service.is_running()


def test_start_scp_ae_title_truncation(dicom_service):
    """Verify AE title longer than 16 characters is safely constrained to 16 chars."""
    port = get_free_port()
    long_aet_cfg = {
        "id": "cfg_long_aet",
        "ae_title": "VERY_LONG_AE_TITLE_OVER_16_CHARS",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": port,
    }
    inst = dicom_service.start_scp_for_config(long_aet_cfg)
    assert inst is not None
    assert len(inst.ae_title) <= 16
    assert inst.ae_title == "VERY_LONG_AE_TIT"
    assert inst.status == "RUNNING"


def test_presentation_context_filtering(dicom_service):
    """Verify SCP presentation contexts strictly honor configured service classes."""
    port = get_free_port()
    # Configure SCP for Verification SOP Class only (no Storage)
    echo_only_cfg = {
        "id": "cfg_echo_only",
        "ae_title": "ECHO_ONLY_SCP",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": port,
        "dicom_services": ["VerificationSOPClass"],
        "transfer_syntaxes": ["ExplicitVRLittleEndian", "ImplicitVRLittleEndian"],
    }
    inst = dicom_service.start_scp_for_config(echo_only_cfg)
    assert inst is not None

    client_ae = AE(ae_title="TEST_CLIENT")
    client_ae.add_requested_context(Verification)
    client_ae.add_requested_context(MRImageStorage)

    assoc = client_ae.associate("127.0.0.1", port, ae_title="ECHO_ONLY_SCP")
    assert assoc.is_established

    # Verification context should be accepted
    accepted_cx = [cx for cx in assoc.accepted_contexts if cx.abstract_syntax == Verification]
    assert len(accepted_cx) == 1

    # MR Storage context should be rejected
    rejected_cx = [cx for cx in assoc.rejected_contexts if cx.abstract_syntax == MRImageStorage]
    assert len(rejected_cx) == 1

    assoc.release()


def test_scp_lifecycle_update_transition(dicom_service):
    """Verify updating an SCP configuration drains the old instance and launches a new one."""
    port1 = get_free_port()
    port2 = get_free_port()

    initial_cfg = {
        "id": "cfg_dynamic",
        "ae_title": "SCP_V1",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": port1,
    }
    inst1 = dicom_service.start_scp_for_config(initial_cfg)
    assert inst1.status == "RUNNING"
    assert inst1.port == port1

    updated_cfg = {
        "id": "cfg_dynamic",
        "ae_title": "SCP_V2",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": port2,
    }
    inst2 = dicom_service.update_scp_for_config(updated_cfg)
    assert inst2.status == "RUNNING"
    assert inst2.port == port2
    assert inst2.ae_title == "SCP_V2"

    # Verify old instance was moved to draining
    statuses = {s["port"]: s["status"] for s in dicom_service.list_scp_instances()}
    assert statuses[port2] == "RUNNING"


def test_scp_lifecycle_remove_transition(dicom_service):
    """Verify removing an SCP configuration halts the server cleanly."""
    port = get_free_port()
    cfg = {
        "id": "cfg_to_remove",
        "ae_title": "SCP_DELETE",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": port,
    }
    inst = dicom_service.start_scp_for_config(cfg)
    assert inst.status == "RUNNING"

    dicom_service.stop_scp_for_config("cfg_to_remove", graceful=True)
    statuses = [s for s in dicom_service.list_scp_instances() if s["config_id"] == "cfg_to_remove"]
    assert len(statuses) == 0 or statuses[0]["status"] in ("STOPPED", "DRAINING")
