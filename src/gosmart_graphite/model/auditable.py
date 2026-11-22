"""Base auditable graph entity models and deterministic ID / relation helpers."""

import json
import uuid
from datetime import datetime
from typing import Any, ClassVar, Dict, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Default namespace for deterministic DICOM graph entity ID derivation
DICOM_GRAPH_NAMESPACE: uuid.UUID = uuid.uuid5(uuid.NAMESPACE_DNS, "gosmart.dicom.graph")

ActionType = Literal["CREATE", "UPDATE", "DELETE", "RESTORE"]


def generate_deterministic_id(
    subset: Dict[str, Any],
    namespace: Optional[uuid.UUID] = None,
) -> str:
    """Generate a deterministic UUIDv5 string from a JSON-serializable property subset.

    Ensures canonical serialization: keys sorted, compact delimiters,
    and UTF-8 encoding before applying UUIDv5 hashing.
    """
    ns = namespace or DICOM_GRAPH_NAMESPACE
    canonical_json = json.dumps(subset, sort_keys=True, separators=(",", ":"), default=str)
    return str(uuid.uuid5(ns, canonical_json))


def to_negated_relation_type(rel_type: str) -> str:
    """Return the negated relation type prefixed with 'NOT_'."""
    return rel_type if rel_type.startswith("NOT_") else f"NOT_{rel_type}"


def to_positive_relation_type(rel_type: str) -> str:
    """Return the positive relation type by stripping the 'NOT_' prefix if present."""
    return rel_type[4:] if rel_type.startswith("NOT_") else rel_type


def is_negated_relation_type(rel_type: str) -> bool:
    """Check if the given relationship type carries the reserved 'NOT_' prefix."""
    return rel_type.startswith("NOT_")


class Auditable(BaseModel):
    """Base auditable entity for all property graph nodes and relationships."""

    id: str = Field(default="", description="System's private deterministically unique ID field")
    test_id: Optional[str] = Field(
        default=None,
        description="Test related identifier. Non-null/non-empty indicates ephemeral test-generated data.",
    )

    @field_validator("test_id", mode="before")
    @classmethod
    def normalize_test_id(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        return v

    Action: ActionType = Field(
        default="CREATE",
        description="Auditable action taken: CREATE, UPDATE, DELETE, or RESTORE",
    )
    ActedAt: Optional[datetime] = Field(
        default=None,
        description="Timestamp of the action normalized to UTC",
    )
    ActedBy: str = Field(
        default="system",
        description="Identifier of the user or agent that caused the action",
    )
    CreatedAt: Optional[datetime] = Field(
        default=None,
        description="Creation timestamp normalized to UTC",
    )
    UpdatedAt: Optional[datetime] = Field(
        default=None,
        description="Last update timestamp normalized to UTC",
    )
    ExpiresAt: Optional[datetime] = Field(
        default=None,
        description="Expiration timestamp. Null implies non-expiring.",
    )

    model_config = {
        "populate_by_name": True,
        "validate_assignment": True,
    }


class AuditableNode(Auditable):
    """Base class for all graph nodes with single static label enforcement."""

    __label__: ClassVar[str] = "Node"

    def get_identity_subset(self) -> Dict[str, Any]:
        """Return the dictionary subset used to compute the deterministic ID."""
        raise NotImplementedError("Subclasses must define get_identity_subset")

    def compute_id(self) -> str:
        """Compute the deterministic ID from the identity subset."""
        return generate_deterministic_id(self.get_identity_subset())

    @model_validator(mode="after")
    def populate_deterministic_id(self) -> "AuditableNode":
        """Automatically populate id if not provided."""
        if not self.id:
            try:
                self.id = self.compute_id()
            except NotImplementedError:
                pass
        return self


class AuditableRelation(Auditable):
    """Base class for all graph relationships with single static type enforcement."""

    __type__: ClassVar[str] = "RELATION"

    start_node_id: str = Field(..., description="Deterministic ID of the source node")
    end_node_id: str = Field(..., description="Deterministic ID of the target node")

    def get_identity_subset(self) -> Dict[str, Any]:
        """Return the dictionary subset used to compute the deterministic relationship ID."""
        return {
            "start_node_id": self.start_node_id,
            "type": self.__type__,
            "end_node_id": self.end_node_id,
        }

    def compute_id(self) -> str:
        """Compute the deterministic relationship ID."""
        return generate_deterministic_id(self.get_identity_subset())

    @classmethod
    def to_negated_type(cls) -> str:
        """Return the negated type for this relationship."""
        return to_negated_relation_type(cls.__type__)

    @classmethod
    def to_positive_type(cls) -> str:
        """Return the positive type for this relationship."""
        return to_positive_relation_type(cls.__type__)

    @classmethod
    def is_negated(cls) -> bool:
        """Return True if this relationship type is currently negated."""
        return is_negated_relation_type(cls.__type__)

    @model_validator(mode="after")
    def populate_deterministic_id(self) -> "AuditableRelation":
        """Automatically populate id if not provided."""
        if not self.id:
            self.id = self.compute_id()
        return self


__all__ = [
    "DICOM_GRAPH_NAMESPACE",
    "ActionType",
    "generate_deterministic_id",
    "to_negated_relation_type",
    "to_positive_relation_type",
    "is_negated_relation_type",
    "Auditable",
    "AuditableNode",
    "AuditableRelation",
]
