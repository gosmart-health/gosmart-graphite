"""Unit tests for gosmart_graphite.model.auditable."""

import uuid
from typing import Any, ClassVar, Dict

import pytest
from pydantic import ValidationError

from gosmart_graphite.model.auditable import (
    DICOM_GRAPH_NAMESPACE,
    Auditable,
    AuditableNode,
    AuditableRelation,
    generate_deterministic_id,
    is_negated_relation_type,
    to_negated_relation_type,
    to_positive_relation_type,
)


class _SampleNode(AuditableNode):
    __label__: ClassVar[str] = "Sample"
    key: str

    def get_identity_subset(self) -> Dict[str, Any]:
        return {"entity": self.__label__, "key": self.key}


class _SampleRelation(AuditableRelation):
    __type__: ClassVar[str] = "LINKS_TO"


class _NegatedSampleRelation(AuditableRelation):
    __type__: ClassVar[str] = "NOT_LINKS_TO"


def test_auditable_defaults_and_test_id_normalization():
    """Verify default audit metadata and test_id whitespace trimming."""
    entity = Auditable(test_id="  session_1  ")
    assert entity.id == ""
    assert entity.test_id == "session_1"
    assert entity.Action == "CREATE"
    assert entity.ActedBy == "system"
    assert entity.ActedAt is None
    assert entity.CreatedAt is None
    assert entity.UpdatedAt is None
    assert entity.ExpiresAt is None

    blank_entity = Auditable(test_id="   ")
    assert blank_entity.test_id is None


def test_auditable_invalid_action_rejected():
    """Verify invalid Action literal raises ValidationError."""
    with pytest.raises(ValidationError):
        Auditable(Action="ARCHIVE")  # type: ignore[arg-type]


def test_auditable_node_deterministic_id_and_override():
    """Verify AuditableNode auto-populates deterministic ID and preserves explicit IDs."""
    node = _SampleNode(key="alpha")
    expected = generate_deterministic_id({"entity": "Sample", "key": "alpha"})
    assert node.__label__ == "Sample"
    assert node.id == expected

    explicit = _SampleNode(id="custom-id-123", key="alpha")
    assert explicit.id == "custom-id-123"


def test_auditable_node_base_without_identity_subset():
    """Verify base AuditableNode leaves id empty when get_identity_subset is not overridden."""
    base_node = AuditableNode()
    assert base_node.__label__ == "Node"
    assert base_node.id == ""
    with pytest.raises(NotImplementedError):
        base_node.get_identity_subset()


def test_auditable_relation_deterministic_id_and_negation():
    """Verify AuditableRelation ID generation and negation classmethods."""
    rel = _SampleRelation(start_node_id="node-a", end_node_id="node-b")
    expected = generate_deterministic_id({"start_node_id": "node-a", "type": "LINKS_TO", "end_node_id": "node-b"})
    assert rel.id == expected
    assert _SampleRelation.to_negated_type() == "NOT_LINKS_TO"
    assert _SampleRelation.to_positive_type() == "LINKS_TO"
    assert _SampleRelation.is_negated() is False

    neg_rel = _NegatedSampleRelation(start_node_id="node-a", end_node_id="node-b")
    assert neg_rel.is_negated() is True
    assert neg_rel.to_positive_type() == "LINKS_TO"
    assert neg_rel.to_negated_type() == "NOT_LINKS_TO"


def test_generate_deterministic_id_canonical_and_custom_namespace():
    """Verify key-order invariance and custom UUID namespace support."""
    id_a = generate_deterministic_id({"b": 2, "a": 1})
    id_b = generate_deterministic_id({"a": 1, "b": 2})
    assert id_a == id_b

    custom_ns = uuid.uuid5(uuid.NAMESPACE_DNS, "custom.example.namespace")
    assert custom_ns != DICOM_GRAPH_NAMESPACE
    assert generate_deterministic_id({"a": 1}, namespace=custom_ns) != generate_deterministic_id({"a": 1})


def test_relation_negation_helpers():
    """Verify standalone relation negation helper functions."""
    assert to_negated_relation_type("SERVES") == "NOT_SERVES"
    assert to_negated_relation_type("NOT_SERVES") == "NOT_SERVES"
    assert to_positive_relation_type("NOT_SERVES") == "SERVES"
    assert to_positive_relation_type("SERVES") == "SERVES"
    assert is_negated_relation_type("NOT_SERVES") is True
    assert is_negated_relation_type("SERVES") is False
