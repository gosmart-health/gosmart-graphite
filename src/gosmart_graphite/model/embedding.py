"""Decoupled vector embedding graph node and :EMBEDS relationship models."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import Field, field_validator, model_validator

from gosmart_graphite.model.auditable import AuditableNode, AuditableRelation, generate_deterministic_id

DEFAULT_EMBEDDING_VECTOR_DIMENSIONS: int = 768
EMBEDDING_VECTOR_INDEX_NAME: str = "embedding_vector_idx"


class DiagnosticReportFieldTag(StrEnum):
    """Canonical targetFieldTag / aspect values for DiagnosticReport embeddings."""

    FINDINGS = "FINDINGS"
    CONCLUSION = "CONCLUSION"


class Embedding(AuditableNode):
    """Auditable graph node storing a decoupled L2-normalized vector embedding (:Embedding)."""

    __label__: ClassVar[str] = "Embedding"

    modelTag: str = Field(
        ...,
        description=(
            "System-specific short string identifying the embedding model used (e.g. LLM_Config.tag 'embedding-v1')"
        ),
    )
    size: int = Field(
        default=0,
        description="Dimensionality (length) of the vector; auto-populated from len(vector) when omitted",
    )
    vector: List[float] = Field(
        ...,
        description="L2-normalized vector representation (array of finite floats), indexed by a vector index",
    )
    text: str = Field(
        ...,
        description="Original source text from which the embedding vector was generated",
    )
    targetFieldTag: str = Field(
        ...,
        description=(
            "Tag identifying the field on the target node that the vector represents (e.g. 'FINDINGS', 'CONCLUSION')"
        ),
    )
    target_node_id: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Deterministic ID of the target node used for target-scoped deterministic Embedding ID derivation",
    )

    @field_validator("modelTag", "targetFieldTag", "text", mode="before")
    @classmethod
    def validate_required_tag(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field must be a non-empty string.")
        return v.strip()

    @field_validator("target_node_id", mode="before")
    @classmethod
    def normalize_target_node_id(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        raise ValueError("target_node_id must be a string or None.")

    @field_validator("vector", mode="before")
    @classmethod
    def validate_vector(cls, v: Any) -> List[float]:
        if v is None or isinstance(v, (str, bytes, bytearray)):
            raise ValueError("vector must be a non-empty sequence of finite floats.")
        try:
            items = list(v)
        except TypeError as exc:
            raise ValueError("vector must be a non-empty sequence of finite floats.") from exc
        if not items:
            raise ValueError("vector must not be empty.")
        result: List[float] = []
        for idx, elem in enumerate(items):
            if isinstance(elem, bool):
                raise ValueError(f"vector element at index {idx} must be a float, not bool.")
            try:
                fval = float(elem)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"vector element at index {idx} is not a valid float.") from exc
            if not math.isfinite(fval):
                raise ValueError(f"vector element at index {idx} must be finite (got {elem!r}).")
            result.append(fval)
        return result

    @field_validator("size", mode="before")
    @classmethod
    def validate_size(cls, v: Any) -> int:
        if v is None:
            return 0
        if isinstance(v, bool):
            raise ValueError("size must be a non-negative integer, not bool.")
        try:
            iv = int(v)
        except (TypeError, ValueError) as exc:
            raise ValueError("size must be an integer.") from exc
        if iv < 0:
            raise ValueError("size must be a positive integer matching len(vector).")
        return iv

    @model_validator(mode="after")
    def validate_size_matches_vector(self) -> "Embedding":
        vec_len = len(self.vector)
        if self.size == 0:
            object.__setattr__(self, "size", vec_len)
        elif self.size != vec_len:
            raise ValueError(f"Embedding size ({self.size}) does not match vector length ({vec_len}).")
        return self

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "modelTag": self.modelTag,
            "targetFieldTag": self.targetFieldTag,
        }
        if self.target_node_id:
            subset["target_node_id"] = self.target_node_id
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset

    @classmethod
    def compute_target_scoped_id(
        cls,
        target_node_id: str,
        model_tag: str,
        target_field_tag: str,
        test_id: Optional[str] = None,
    ) -> str:
        """Compute a deterministic UUIDv5 scoped to (target_node_id, modelTag, targetFieldTag[, test_id])."""
        clean_target = target_node_id.strip() if isinstance(target_node_id, str) else ""
        clean_model_tag = model_tag.strip() if isinstance(model_tag, str) else ""
        clean_field_tag = target_field_tag.strip() if isinstance(target_field_tag, str) else ""
        if not clean_target or not clean_model_tag or not clean_field_tag:
            raise ValueError(
                "target_node_id, model_tag, and target_field_tag must all be non-empty "
                "to compute a target-scoped Embedding ID."
            )
        subset: Dict[str, Any] = {
            "entity": cls.__label__,
            "target_node_id": clean_target,
            "modelTag": clean_model_tag,
            "targetFieldTag": clean_field_tag,
        }
        if test_id and str(test_id).strip():
            subset["test_id"] = str(test_id).strip()
        return generate_deterministic_id(subset)

    def to_neo4j_properties(self) -> Dict[str, Any]:
        """Serialize model fields for Neo4j property storage."""
        data = self.model_dump()
        for k, val in list(data.items()):
            if isinstance(val, datetime):
                data[k] = val.isoformat()
        return data


EmbeddingNode = Embedding


class EmbedsRelation(AuditableRelation):
    """Relationship (:EMBEDS) connecting (:Embedding)-[:EMBEDS]->(target domain node)."""

    __type__: ClassVar[str] = "EMBEDS"

    aspect: Optional[str] = Field(
        default=None,
        description=(
            "Tag identifying the aspect of the target node associated with the vector (e.g. 'FINDINGS', 'CONCLUSION')"
        ),
    )

    @field_validator("aspect", mode="before")
    @classmethod
    def normalize_aspect(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        raise ValueError("aspect must be a string or None.")


__all__ = [
    "DEFAULT_EMBEDDING_VECTOR_DIMENSIONS",
    "EMBEDDING_VECTOR_INDEX_NAME",
    "DiagnosticReportFieldTag",
    "Embedding",
    "EmbeddingNode",
    "EmbedsRelation",
]
