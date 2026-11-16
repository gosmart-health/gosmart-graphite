from datetime import datetime

from gosmart_graphite.model.graph import (
    DICOMConfigurationNode,
    HasDicomConfigurationRelation,
    SystemNode,
    generate_deterministic_id,
)


def test_system_node_unit():
    """Verify SystemNode creation, deterministic ID, and properties."""
    sys = SystemNode(
        name="Test VNA",
        owner="Test Hospital",
        address="123 Ocean Ave",
        email="vna@testhospital.org",
        phone="+1 555 123 4567",
        serial_number="TEST-SN-001",
    )
    assert sys.__label__ == "System"
    assert sys.id != ""
    assert sys.name == "Test VNA"
    assert sys.owner == "Test Hospital"
    assert sys.address == "123 Ocean Ave"
    assert sys.email == "vna@testhospital.org"
    assert sys.phone == "+1 555 123 4567"
    assert sys.serial_number == "TEST-SN-001"
    assert isinstance(sys.installed_at, datetime)

    # Verify deterministic ID repeatability
    sys2 = SystemNode()
    assert sys.id == sys2.id, "Singleton System node without test_id must have identical deterministic ID"


def test_dicom_configuration_node_unit():
    """Verify DICOMConfigurationNode promiscuous mode and deterministic ID."""
    cfg_promiscuous = DICOMConfigurationNode(ae_title="*", port=11112)
    assert cfg_promiscuous.__label__ == "DICOMConfiguration"
    assert cfg_promiscuous.is_promiscuous is True
    assert cfg_promiscuous.id != ""

    cfg_specific = DICOMConfigurationNode(ae_title="CT_SCANNER", host="192.168.1.50", port=104)
    assert cfg_specific.is_promiscuous is False
    assert cfg_specific.ae_title == "CT_SCANNER"

    # Deterministic ID check
    expected_id = generate_deterministic_id(
        {"entity": "DICOMConfiguration", "ae_title": "CT_SCANNER", "host": "192.168.1.50", "port": 104}
    )
    assert cfg_specific.id == expected_id


def test_has_dicom_configuration_relation_unit():
    """Verify HasDicomConfigurationRelation type and soft-delete negation helpers."""
    rel = HasDicomConfigurationRelation(start_node_id="sys_1", end_node_id="cfg_1")
    assert rel.__type__ == "HAS_DICOM_CONFIGURATION"
    assert rel.to_negated_type() == "NOT_HAS_DICOM_CONFIGURATION"
    assert rel.id != ""
