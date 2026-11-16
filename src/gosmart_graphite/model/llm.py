"""Domain models and API schemas for dynamic, group-scoped LLM configurations."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

from gosmart_graphite.core.security import resolve_llm_api_key
from gosmart_graphite.model.graph import AuditableNode, AuditableRelation


def _parse_extra_config(v: Any) -> Optional[Dict[str, Any]]:
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        stripped = v.strip()
        if not stripped:
            return None
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError("extra_config JSON string must deserialize to an object/dict.")
        return parsed
    raise ValueError("extra_config must be a JSON object/dict or JSON string.")


class LLM_Config(AuditableNode):
    """Auditable graph node capturing a purpose-tagged, group-sponsored LLM configuration."""

    __label__: ClassVar[str] = "LLM_Config"

    tag: str = Field(..., description="Globally unique purpose tag/key (e.g. 'embedding', 'router', 'rag')")
    provider: str = Field(
        ...,
        description="LLM provider name normalized lowercase (e.g. 'ollama', 'vertexai', 'googleai')",
    )
    model: str = Field(..., description="Canonical model name (e.g. 'nomic-embed-text:latest', 'gemini-embedding-001')")
    url: str = Field(default="", description="Optional API base URL (e.g. 'http://10.200.0.103:11434')")
    dimensions: Optional[int] = Field(
        default=None,
        description="Native or requested output vector dimensionality (e.g. 768)",
    )
    dimensions_truncated: Optional[int] = Field(
        default=None,
        description="Optional Matryoshka truncated output vector dimensionality",
    )
    api_key: str = Field(
        default="",
        description="API key literal, '${ENV_VAR}' reference, or encrypted 'enc:fernet:...' value",
    )
    location: str = Field(
        default="us-central1",
        description="Canonical cloud service location/region string (default: 'us-central1')",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Optional Google Cloud Project ID for Vertex AI",
    )
    extra_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional provider/model parameters (temperature, thinking_level, timeout, etc.)",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional human-readable description",
    )

    @field_validator("tag", "model", mode="before")
    @classmethod
    def validate_required_str(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field must be a non-empty string.")
        return v.strip()

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("provider must be a non-empty string.")
        return v.strip().lower()

    @field_validator("url", "api_key", mode="before")
    @classmethod
    def normalize_optional_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(cls, v: Any) -> str:
        if v is None or not str(v).strip():
            return "us-central1"
        return str(v).strip()

    @field_validator("project_id", mode="before")
    @classmethod
    def normalize_project_id(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("dimensions", "dimensions_truncated", mode="before")
    @classmethod
    def validate_positive_dim(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        iv = int(v)
        if iv <= 0:
            raise ValueError("Dimensions must be a positive integer.")
        return iv

    @field_validator("extra_config", mode="before")
    @classmethod
    def validate_extra_config(cls, v: Any) -> Optional[Dict[str, Any]]:
        return _parse_extra_config(v)

    @model_validator(mode="after")
    def validate_dimension_bounds(self) -> "LLM_Config":
        if (
            self.dimensions is not None
            and self.dimensions_truncated is not None
            and self.dimensions_truncated > self.dimensions
        ):
            raise ValueError(
                f"dimensions_truncated ({self.dimensions_truncated}) cannot exceed dimensions ({self.dimensions})."
            )
        return self

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "tag": self.tag,
        }
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset

    def resolve_api_key(self, encryption_key: Optional[str] = None) -> str:
        """Decrypt and expand any ${ENV_VAR} references in api_key for runtime client use."""
        return resolve_llm_api_key(self.api_key, encryption_key=encryption_key)

    def to_neo4j_properties(self) -> Dict[str, Any]:
        """Serialize model fields for Neo4j property storage (JSON-encoding extra_config)."""
        data = self.model_dump()
        if self.extra_config is not None:
            data["extra_config"] = json.dumps(self.extra_config, sort_keys=True)
        else:
            data["extra_config"] = None
        for k, val in list(data.items()):
            if isinstance(val, datetime):
                data[k] = val.isoformat()
        return data

    def client_fingerprint(
        self,
        resolved_api_key: Optional[str] = None,
        fallback_project_id: Optional[str] = None,
    ) -> Tuple[Any, ...]:
        """Return a stable, hashable tuple identifying the Genkit client parameters."""
        effective_key = resolved_api_key if resolved_api_key is not None else self.api_key
        effective_project = self.project_id or fallback_project_id or ""
        return (
            self.provider,
            self.url,
            effective_key,
            self.location,
            effective_project,
        )


LLMConfigNode = LLM_Config


class HasLlmConfigRelation(AuditableRelation):
    """Relationship connecting sponsoring Group -> LLM_Config."""

    __type__: ClassVar[str] = "HAS_LLM_CONFIG"


class LLMConfigCreateRequest(BaseModel):
    """API / CLI request model to create or overwrite an LLM_Config."""

    tag: str = Field(..., description="Globally unique purpose tag (e.g. 'embedding', 'router', 'rag')")
    provider: str = Field(..., description="LLM provider ('ollama', 'vertexai', 'googleai', etc.)")
    model: str = Field(..., description="Canonical model name")
    url: str = Field(default="", description="Optional API base URL")
    dimensions: Optional[int] = Field(default=None, description="Native or requested output dimensionality")
    dimensions_truncated: Optional[int] = Field(
        default=None,
        description="Optional Matryoshka truncated dimensionality",
    )
    api_key: str = Field(default="", description="Literal API key or '${ENV_VAR}' reference")
    location: str = Field(default="us-central1", description="Cloud service location/region")
    project_id: Optional[str] = Field(default=None, description="Optional GCP project ID")
    extra_config: Optional[Dict[str, Any]] = Field(default=None, description="Optional extra provider/model settings")
    description: Optional[str] = Field(default=None, description="Optional human-readable description")
    assign_group: Optional[str] = Field(
        default=None,
        description="Optional Group ID, name, or code to assign (defaults to 'GoSmart Health')",
    )
    test_id: Optional[str] = Field(default=None, description="Test identifier if ephemeral")

    @field_validator("tag", "model", mode="before")
    @classmethod
    def validate_required_str(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Field must be a non-empty string.")
        return v.strip()

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("provider must be a non-empty string.")
        return v.strip().lower()

    @field_validator("url", "api_key", mode="before")
    @classmethod
    def normalize_optional_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(cls, v: Any) -> str:
        if v is None or not str(v).strip():
            return "us-central1"
        return str(v).strip()

    @field_validator("project_id", "assign_group", "test_id", mode="before")
    @classmethod
    def normalize_nullable_str(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        return v

    @field_validator("dimensions", "dimensions_truncated", mode="before")
    @classmethod
    def validate_positive_dim(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        iv = int(v)
        if iv <= 0:
            raise ValueError("Dimensions must be a positive integer.")
        return iv

    @field_validator("extra_config", mode="before")
    @classmethod
    def validate_extra_config(cls, v: Any) -> Optional[Dict[str, Any]]:
        return _parse_extra_config(v)

    @model_validator(mode="after")
    def validate_dimension_bounds(self) -> "LLMConfigCreateRequest":
        if (
            self.dimensions is not None
            and self.dimensions_truncated is not None
            and self.dimensions_truncated > self.dimensions
        ):
            raise ValueError(
                f"dimensions_truncated ({self.dimensions_truncated}) cannot exceed dimensions ({self.dimensions})."
            )
        return self


class LLMConfigUpdateRequest(BaseModel):
    """API request model to update mutable properties of an existing LLM_Config."""

    provider: Optional[str] = Field(default=None, description="LLM provider")
    model: Optional[str] = Field(default=None, description="Canonical model name")
    url: Optional[str] = Field(default=None, description="Optional API base URL")
    dimensions: Optional[int] = Field(default=None, description="Native or requested output dimensionality")
    dimensions_truncated: Optional[int] = Field(
        default=None,
        description="Optional Matryoshka truncated dimensionality",
    )
    api_key: Optional[str] = Field(default=None, description="Literal API key or '${ENV_VAR}' reference")
    location: Optional[str] = Field(default=None, description="Cloud service location/region")
    project_id: Optional[str] = Field(default=None, description="Optional GCP project ID")
    extra_config: Optional[Dict[str, Any]] = Field(default=None, description="Optional extra provider/model settings")
    description: Optional[str] = Field(default=None, description="Optional human-readable description")
    assign_group: Optional[str] = Field(default=None, description="Optional Group ID, name, or code to also assign")

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            raise ValueError("provider cannot be empty.")
        return v.strip().lower()

    @field_validator("model", mode="before")
    @classmethod
    def normalize_model(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            raise ValueError("model cannot be empty.")
        return v.strip()

    @field_validator("dimensions", "dimensions_truncated", mode="before")
    @classmethod
    def validate_positive_dim(cls, v: Any) -> Optional[int]:
        if v is None or v == "":
            return None
        iv = int(v)
        if iv <= 0:
            raise ValueError("Dimensions must be a positive integer.")
        return iv

    @field_validator("extra_config", mode="before")
    @classmethod
    def validate_extra_config(cls, v: Any) -> Optional[Dict[str, Any]]:
        return _parse_extra_config(v)


class LLMConfigAssignRequest(BaseModel):
    """API request model to assign an LLM_Config to a Group."""

    group: str = Field(..., description="Target Group ID, name, or code")
    test_id: Optional[str] = Field(default=None, description="Optional test identifier")


class LLMConfigGroupSummary(BaseModel):
    """Lightweight Group summary attached to an LLMConfigResponse."""

    id: str
    name: str
    code: str
    group_type: Optional[str] = None


class LLMConfigResponse(BaseModel):
    """Sanitized API response model for an LLM_Config (api_key is always redacted/masked)."""

    id: str = Field(..., description="Deterministic configuration ID")
    tag: str = Field(..., description="Purpose tag")
    provider: str = Field(..., description="LLM provider")
    model: str = Field(..., description="Canonical model name")
    url: str = Field(default="", description="API base URL")
    dimensions: Optional[int] = Field(default=None, description="Native or requested output dimensionality")
    dimensions_truncated: Optional[int] = Field(default=None, description="Truncated output dimensionality")
    api_key: str = Field(default="", description="Masked/redacted API key or '${ENV_VAR}' reference")
    has_api_key: bool = Field(default=False, description="True if a non-empty api_key is configured")
    api_key_source: str = Field(
        default="none",
        description="Storage format: 'none' | 'env' | 'encrypted' | 'plaintext'",
    )
    location: str = Field(default="us-central1", description="Cloud service location/region")
    project_id: Optional[str] = Field(default=None, description="Optional GCP project ID")
    extra_config: Optional[Dict[str, Any]] = Field(default=None, description="Optional extra provider/model settings")
    description: Optional[str] = Field(default=None, description="Optional description")
    group_ids: List[str] = Field(default_factory=list, description="Associated Group IDs via HAS_LLM_CONFIG")
    groups: List[LLMConfigGroupSummary] = Field(default_factory=list, description="Associated Group summaries")
    test_id: Optional[str] = Field(default=None, description="Test identifier if ephemeral")
    action: Optional[str] = Field(default="CREATE", description="Auditable action state")
    created_at: Optional[datetime] = Field(default=None, description="Created timestamp (UTC)")
    updated_at: Optional[datetime] = Field(default=None, description="Updated timestamp (UTC)")

    model_config = {"populate_by_name": True}
