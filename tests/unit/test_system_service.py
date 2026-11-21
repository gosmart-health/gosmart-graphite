from datetime import datetime

from gosmart_graphite.model.graph import (
    DICOMConfigurationNode,
    FHIRConfigurationNode,
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
    """Verify DICOMConfigurationNode promiscuous mode, storage_root_uri default, and deterministic ID."""
    cfg_promiscuous = DICOMConfigurationNode(ae_title="*", port=11112)
    assert cfg_promiscuous.__label__ == "DICOMConfiguration"
    assert cfg_promiscuous.is_promiscuous is True
    assert cfg_promiscuous.storage_root_uri == "/tmp/gosmart_graphite/*"
    assert cfg_promiscuous.id != ""

    cfg_specific = DICOMConfigurationNode(ae_title="CT_SCANNER", host="192.168.1.50", port=104)
    assert cfg_specific.is_promiscuous is False
    assert cfg_specific.ae_title == "CT_SCANNER"
    assert cfg_specific.storage_root_uri == "/tmp/gosmart_graphite/CT_SCANNER"

    # Explicit storage_root_uri is preserved, and missing/blank values fall back to default
    cfg_explicit = DICOMConfigurationNode(
        ae_title="CT_SCANNER",
        host="192.168.1.50",
        port=104,
        storage_root_uri="gs://my-dicom-bucket/ct",
    )
    assert cfg_explicit.storage_root_uri == "gs://my-dicom-bucket/ct"
    assert cfg_explicit.id == cfg_specific.id

    cfg_none = DICOMConfigurationNode(ae_title="MRI_1", storage_root_uri=None)
    assert cfg_none.storage_root_uri == "/tmp/gosmart_graphite/MRI_1"

    cfg_blank = DICOMConfigurationNode(ae_title="MRI_2", storage_root_uri="   ")
    assert cfg_blank.storage_root_uri == "/tmp/gosmart_graphite/MRI_2"

    # Assignment validation should also re-apply default when cleared
    cfg_explicit.storage_root_uri = ""
    assert cfg_explicit.storage_root_uri == "/tmp/gosmart_graphite/CT_SCANNER"

    # Deterministic ID check
    expected_id = generate_deterministic_id(
        {"entity": "DICOMConfiguration", "ae_title": "CT_SCANNER", "host": "192.168.1.50", "port": 104}
    )
    assert cfg_specific.id == expected_id


def test_fhir_configuration_node_unit():
    """Verify FHIRConfigurationNode defaults, fields, and deterministic ID."""
    cfg_default = FHIRConfigurationNode()
    assert cfg_default.__label__ == "FHIRConfiguration"
    assert cfg_default.accession_system == ""
    assert cfg_default.patient_id_system == ""
    assert cfg_default.url == ""
    assert cfg_default.poll_sec == 120
    assert cfg_default.id != ""

    cfg = FHIRConfigurationNode(
        accession_system="urn:oid:1.2.3.4",
        patient_id_system="urn:oid:2.16.840.1.113883.4.3.25",
        url="https://fhir.example.org/r4",
        poll_sec=60,
    )
    assert cfg.accession_system == "urn:oid:1.2.3.4"
    assert cfg.patient_id_system == "urn:oid:2.16.840.1.113883.4.3.25"
    assert cfg.url == "https://fhir.example.org/r4"
    assert cfg.poll_sec == 60

    expected_id = generate_deterministic_id({"entity": "FHIRConfiguration", "url": "https://fhir.example.org/r4"})
    assert cfg.id == expected_id

    cfg_test = FHIRConfigurationNode(url="https://fhir.example.org/r4", test_id="t1")
    assert cfg_test.id == generate_deterministic_id(
        {"entity": "FHIRConfiguration", "url": "https://fhir.example.org/r4", "test_id": "t1"}
    )


def test_has_dicom_configuration_relation_unit():
    """Verify HasDicomConfigurationRelation type and soft-delete negation helpers."""
    rel = HasDicomConfigurationRelation(start_node_id="sys_1", end_node_id="cfg_1")
    assert rel.__type__ == "HAS_DICOM_CONFIGURATION"
    assert rel.to_negated_type() == "NOT_HAS_DICOM_CONFIGURATION"
    assert rel.id != ""
