from gosmart_graphite.model.auditable import (
    generate_deterministic_id,
    is_negated_relation_type,
    to_negated_relation_type,
    to_positive_relation_type,
)
from gosmart_graphite.model.graph import (
    HasInstanceRelation,
    HasSeriesRelation,
    HasStudyRelation,
    InstanceNode,
    PatientNode,
    SeriesNode,
    StudyNode,
)
from gosmart_graphite.service.dicom_service import dicom_service

# Unit Tests for Auditable Models & Deterministic ID Generation


def test_deterministic_id_generation():
    """Verify deterministic UUIDv5 generation from JSON subsets regardless of key order."""
    subset_a = {"patient_id": "P1001", "entity": "Patient"}
    subset_b = {"entity": "Patient", "patient_id": "P1001"}

    id_a = generate_deterministic_id(subset_a)
    id_b = generate_deterministic_id(subset_b)

    assert id_a == id_b, "Keys order must not affect deterministic ID"
    assert len(id_a) == 36, "UUIDv5 must be a 36-character canonical string"

    # Different subset must produce different ID
    subset_c = {"entity": "Patient", "patient_id": "P1002"}
    id_c = generate_deterministic_id(subset_c)
    assert id_a != id_c, "Different entity IDs must produce different deterministic IDs"


def test_auditable_node_and_relation_classes():
    """Verify AuditableNode and AuditableRelation single label and type enforcement."""
    patient = PatientNode(patient_id="P1001", patient_name="DOE^JOHN", test_id="test_run_1")
    assert patient.__label__ == "Patient"
    assert patient.Action == "CREATE"
    assert patient.ActedBy == "system"
    assert patient.id != ""
    assert patient.test_id == "test_run_1"

    study = StudyNode(study_instance_uid="1.2.3.4.5")
    assert study.__label__ == "Study"
    assert study.id != ""

    rel = HasStudyRelation(start_node_id=patient.id, end_node_id=study.id)
    assert rel.__type__ == "HAS_STUDY"
    assert rel.id != ""
    assert not rel.is_negated()
    assert rel.to_negated_type() == "NOT_HAS_STUDY"
    assert HasStudyRelation.to_positive_type() == "HAS_STUDY"


def test_relation_negation_helpers():
    """Verify relation type negation helpers."""
    assert to_negated_relation_type("HAS_STUDY") == "NOT_HAS_STUDY"
    assert to_negated_relation_type("NOT_HAS_STUDY") == "NOT_HAS_STUDY"
    assert to_positive_relation_type("NOT_HAS_STUDY") == "HAS_STUDY"
    assert to_positive_relation_type("HAS_STUDY") == "HAS_STUDY"
    assert is_negated_relation_type("NOT_HAS_STUDY") is True
    assert is_negated_relation_type("HAS_STUDY") is False


def test_hierarchy_to_nodes_and_relations():
    """Verify DicomHierarchy conversion to AuditableNode and AuditableRelation objects with instance provenance."""
    ds = dicom_service.create_mock_dicom(patient_id="MOCK_TEST_1")
    hierarchy = dicom_service.parse_dataset(ds)
    hierarchy.test_id = "test_session_1"
    hierarchy.acted_by = "test_runner"
    hierarchy.instance.source = "dimse:10.200.0.123"
    hierarchy.instance.source_identity = "ae:MICRODICOM"
    hierarchy.instance.destination = "dimse:0.0.0.0:11112"
    hierarchy.instance.destination_identity = "ae:GOSMART_GS"

    p, s, se, i = hierarchy.to_nodes()
    assert isinstance(p, PatientNode)
    assert isinstance(s, StudyNode)
    assert isinstance(se, SeriesNode)
    assert isinstance(i, InstanceNode)
    assert p.test_id == "test_session_1"
    assert p.ActedBy == "test_runner"

    # Instance-level provenance assertions
    assert i.Source == "dimse:10.200.0.123"
    assert i.SourceIdentity == "ae:MICRODICOM"
    assert i.Destination == "dimse:0.0.0.0:11112"
    assert i.DestinationIdentity == "ae:GOSMART_GS"

    # Verify provenance fields are strictly on Instance level
    assert not hasattr(p, "Source")
    assert not hasattr(s, "Source")
    assert not hasattr(se, "Source")
    assert not hasattr(p, "Destination")
    assert not hasattr(s, "Destination")
    assert not hasattr(se, "Destination")

    r_ps, r_se, r_ei = hierarchy.to_relations(p, s, se, i)
    assert isinstance(r_ps, HasStudyRelation)
    assert isinstance(r_se, HasSeriesRelation)
    assert isinstance(r_ei, HasInstanceRelation)
    assert r_ps.start_node_id == p.id
    assert r_ps.end_node_id == s.id
    assert r_ps.test_id == "test_session_1"
