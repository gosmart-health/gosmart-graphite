"""Domain models and schemas for group-scoped FHIR configurations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, List, Optional

from pydantic import BaseModel, Field, field_validator

from gosmart_graphite.model.auditable import AuditableRelation


class HasFhirConfigRelation(AuditableRelation):
    """Relationship connecting sponsoring Group -> FHIRConfiguration."""

    __type__: ClassVar[str] = "HAS_FHIR_CONFIG"


class FHIRConfigurationCreateRequest(BaseModel):
    """API / CLI request model to register a FHIRConfiguration node."""

    url: str = Field(..., description="FHIR server base URL (e.g. 'https://fhir.example.org/r4')")
    accession_system: str = Field(
        default="",
        description="Identifier system URI for Accession Number",
    )
    patient_id_system: str = Field(
        default="",
        description="Identifier system URI for Patient ID / MRN",
    )
    poll_sec: int = Field(
        default=120,
        description="Polling interval in seconds for FHIR updates",
    )
    group_id: Optional[str] = Field(
        default=None,
        description="Optional exact Group ID to associate via HAS_FHIR_CONFIG (defaults to 'GoSmart Health')",
    )
    test_id: Optional[str] = Field(default=None, description="Test identifier if ephemeral")

    @field_validator("url", mode="before")
    @classmethod
    def validate_required_url(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("url must be a non-empty string.")
        return v.strip()

    @field_validator("accession_system", "patient_id_system", mode="before")
    @classmethod
    def normalize_optional_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("poll_sec", mode="before")
    @classmethod
    def validate_positive_poll_sec(cls, v: Any) -> int:
        if v is None or v == "":
            return 120
        iv = int(v)
        if iv <= 0:
            raise ValueError("poll_sec must be a positive integer.")
        return iv

    @field_validator("group_id", "test_id", mode="before")
    @classmethod
    def normalize_nullable_str(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        return v


class FHIRConfigurationGroupSummary(BaseModel):
    """Lightweight Group summary attached to a FHIRConfigurationResponse."""

    id: str
    name: str
    code: str
    group_type: Optional[str] = None


class FHIRConfigurationResponse(BaseModel):
    """Response model for a FHIRConfiguration node."""

    id: str = Field(..., description="Deterministic configuration ID")
    url: str = Field(..., description="FHIR server base URL")
    accession_system: str = Field(default="", description="Accession Number system URI")
    patient_id_system: str = Field(default="", description="Patient ID / MRN system URI")
    poll_sec: int = Field(default=120, description="Polling interval in seconds")
    group_ids: List[str] = Field(default_factory=list, description="Associated Group IDs via HAS_FHIR_CONFIG")
    groups: List[FHIRConfigurationGroupSummary] = Field(default_factory=list, description="Associated Group summaries")
    test_id: Optional[str] = Field(default=None, description="Test identifier if ephemeral")
    action: Optional[str] = Field(default="CREATE", description="Auditable action state")
    created_at: Optional[datetime] = Field(default=None, description="Created timestamp (UTC)")
    updated_at: Optional[datetime] = Field(default=None, description="Updated timestamp (UTC)")

    model_config = {"populate_by_name": True}
