"""Unit tests for gosmart_graphite.model.dicom_config."""

from gosmart_graphite.model.auditable import generate_deterministic_id
from gosmart_graphite.model.dicom_config import (
    DICOMConfigurationCreateRequest,
    DICOMConfigurationGroupSummary,
    DICOMConfigurationNode,
    DICOMConfigurationResponse,
    DICOMConfigurationUpdateRequest,
    HasDicomConfigurationRelation,
)


def test_dicom_configuration_node_promiscuous_and_storage_defaults():
    """Verify promiscuous flag derivation and default storage_root_uri."""
    promiscuous = DICOMConfigurationNode(ae_title="*", port=11112)
    assert promiscuous.__label__ == "DICOMConfiguration"
    assert promiscuous.is_promiscuous is True
    assert promiscuous.storage_root_uri == "/tmp/gosmart_graphite/*"

    specific = DICOMConfigurationNode(ae_title="CT_01", host="10.0.0.5", port=104, storage_root_uri="  ")
    assert specific.is_promiscuous is False
    assert specific.storage_root_uri == "/tmp/gosmart_graphite/CT_01"

    trimmed = DICOMConfigurationNode(ae_title="MR_01", storage_root_uri="  gs://bucket/dicom  ")
    assert trimmed.storage_root_uri == "gs://bucket/dicom"


def test_dicom_configuration_node_deterministic_id_with_and_without_test_id():
    """Verify deterministic ID includes test_id only when non-empty."""
    prod_cfg = DICOMConfigurationNode(ae_title="PACS", host="192.168.1.10", port=4242)
    expected_prod = generate_deterministic_id(
        {"entity": "DICOMConfiguration", "ae_title": "PACS", "host": "192.168.1.10", "port": 4242}
    )
    assert prod_cfg.id == expected_prod

    test_cfg = DICOMConfigurationNode(ae_title="PACS", host="192.168.1.10", port=4242, test_id="t1")
    expected_test = generate_deterministic_id(
        {
            "entity": "DICOMConfiguration",
            "ae_title": "PACS",
            "host": "192.168.1.10",
            "port": 4242,
            "test_id": "t1",
        }
    )
    assert test_cfg.id == expected_test
    assert test_cfg.id != prod_cfg.id


def test_has_dicom_configuration_relation():
    """Verify HasDicomConfigurationRelation type, deterministic ID, and negation."""
    rel = HasDicomConfigurationRelation(start_node_id="sys-1", end_node_id="cfg-1")
    assert rel.__type__ == "HAS_DICOM_CONFIGURATION"
    assert rel.id == generate_deterministic_id(
        {"start_node_id": "sys-1", "type": "HAS_DICOM_CONFIGURATION", "end_node_id": "cfg-1"}
    )
    assert rel.to_negated_type() == "NOT_HAS_DICOM_CONFIGURATION"
    assert rel.to_positive_type() == "HAS_DICOM_CONFIGURATION"
    assert rel.is_negated() is False


def test_dicom_configuration_dto_models():
    """Verify request/response DTO defaults, normalization, and serialization."""
    create_req = DICOMConfigurationCreateRequest(ae_title="ORTHANC", host="127.0.0.1", port=4242, test_id="  ")
    assert create_req.test_id is None
    assert create_req.roles == ["SCP"]

    create_req_with_test = DICOMConfigurationCreateRequest(ae_title="ORTHANC", test_id="  smoke  ")
    assert create_req_with_test.test_id == "smoke"

    update_req = DICOMConfigurationUpdateRequest(port=11113, description="Updated port")
    assert update_req.port == 11113
    assert update_req.ae_title is None

    group = DICOMConfigurationGroupSummary(id="grp-1", name="Radiology", code="RAD", group_type="DEPARTMENT")
    resp = DICOMConfigurationResponse(
        id="cfg-1",
        ae_title="ORTHANC",
        host="127.0.0.1",
        port=4242,
        group_ids=[group.id],
        groups=[group],
    )
    assert resp.groups[0].code == "RAD"
    assert resp.action == "CREATE"
