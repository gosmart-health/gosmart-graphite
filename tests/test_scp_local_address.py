import socket

from gosmart_graphite.service.pynetdicom_service import is_local_address, pynetdicom_service


def get_free_port() -> int:
    """Find and return an ephemeral free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_is_local_address_detection():
    """Verify is_local_address correctly identifies local vs remote addresses."""
    assert is_local_address(None) is True
    assert is_local_address("") is True
    assert is_local_address("0.0.0.0") is True
    assert is_local_address("127.0.0.1") is True
    assert is_local_address("localhost") is True
    assert is_local_address("::") is True
    assert is_local_address("::1") is True

    # Remote / non-local IPs must return False
    assert is_local_address("8.8.8.8") is False
    assert is_local_address("192.0.2.1") is False  # TEST-NET-1 (RFC 5737)
    assert is_local_address("198.51.100.1") is False  # TEST-NET-2


def test_start_scp_skips_remote_address():
    """Verify start_scp_for_config skips remote destinations and does not attempt local binding."""
    remote_cfg = {
        "id": "cfg_remote_dest",
        "ae_title": "REMOTE_VIEWER",
        "roles": ["SCU", "SCP"],
        "host": "198.51.100.25",  # Remote documentation IP
        "port": 11113,
        "dicom_services": ["*"],
        "transfer_syntaxes": ["*"],
    }
    inst = pynetdicom_service.start_scp_for_config(remote_cfg)
    assert inst is None
    # Verify it is not registered in active instances
    assert pynetdicom_service.get_instance("cfg_remote_dest") is None


def test_start_scp_allows_local_address():
    """Verify start_scp_for_config starts listener for local address."""
    port = get_free_port()
    local_cfg = {
        "id": "cfg_local_test",
        "ae_title": "LOCAL_SCP_TEST",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": port,
        "dicom_services": ["VerificationSOPClass"],
        "transfer_syntaxes": ["*"],
    }
    inst = pynetdicom_service.start_scp_for_config(local_cfg)
    try:
        assert inst is not None
        assert inst.status == "RUNNING"
        assert inst.server is not None
        assert pynetdicom_service.get_instance("cfg_local_test") is not None
    finally:
        if inst and inst.server:
            inst.server.shutdown()
            pynetdicom_service.on_configuration_removed("cfg_local_test")
