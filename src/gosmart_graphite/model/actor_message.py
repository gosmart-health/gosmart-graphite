"""Pydantic message models for Pykka actor communication."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gosmart_graphite.model.auth import Group

DEFAULT_EMBEDDING_TAG = "embedding-v1"


class DicomIngestionMessage(BaseModel):
    """Validated message payload for DicomIngestionActor INGEST_FILE requests."""

    type: Literal["INGEST_FILE"] = Field(
        default="INGEST_FILE",
        description="Actor message type discriminator",
    )
    file_path: str = Field(
        ...,
        min_length=1,
        description="Local path to the DICOM file to ingest",
    )
    test_id: Optional[str] = Field(
        default=None,
        description="Optional test identifier to mark derived graph entities as ephemeral test data",
    )
    acted_by: str = Field(
        default="actor",
        description="Identifier of the user or agent initiating the ingestion",
    )
    source: Optional[str] = Field(
        default=None,
        description="Ingestion stream source (e.g. dimse:<ip>, stow:<ip>, file:<path>)",
    )
    source_identity: Optional[str] = Field(
        default=None,
        description="Identity of stream sender (e.g. ae:<AE_title>, user:<user_id>)",
    )
    destination: Optional[str] = Field(
        default=None,
        description="Local destination endpoint",
    )
    destination_identity: Optional[str] = Field(
        default=None,
        description="Identity of receiving entity",
    )
    group_id: Optional[str] = Field(
        default=None,
        description="Optional associated Group deterministic ID for ReBAC tenancy",
    )
    storage_root_uri: Optional[str] = Field(
        default=None,
        description="Optional explicit destination storage root URI (overrides AE config and global settings)",
    )
    cleanup_source: bool = Field(
        default=False,
        description="If True, remove the local staging file_path after successful destination storage and graph save",
    )
    association_id: Optional[str] = Field(
        default=None,
        description="Optional DICOM association or batch session ID for FHIR pull deduplication",
    )

    @field_validator("file_path", mode="before")
    @classmethod
    def validate_file_path(cls, v: Any) -> str:
        if isinstance(v, Path):
            v = str(v)
        if not isinstance(v, str) or not v.strip():
            raise ValueError("file_path must be a non-empty string")
        return v.strip()

    @field_validator("acted_by", mode="before")
    @classmethod
    def normalize_acted_by(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "actor"
        return v

    @field_validator("association_id", mode="before")
    @classmethod
    def normalize_association_id(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        return v


class DicomAssociationReleasedMessage(BaseModel):
    """Validated message payload notifying DicomIngestionActor that an association/batch has closed."""

    type: Literal["ASSOCIATION_RELEASED"] = Field(
        default="ASSOCIATION_RELEASED",
        description="Actor message type discriminator",
    )
    association_id: str = Field(
        ...,
        min_length=1,
        description="Identifier of the released DICOM association or batch session",
    )

    @field_validator("association_id", mode="before")
    @classmethod
    def validate_association_id(cls, v: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("association_id must be a non-empty string")
        return v.strip()


class DicomMockIngestionMessage(BaseModel):
    """Validated message payload for DicomIngestionActor INGEST_MOCK requests."""

    type: Literal["INGEST_MOCK"] = Field(
        default="INGEST_MOCK",
        description="Actor message type discriminator",
    )
    patient_id: str = Field(
        default="MOCK_PATIENT",
        description="Patient ID for generated mock DICOM dataset",
    )
    modality: str = Field(
        default="MR",
        description="DICOM modality code (e.g. CT, MR)",
    )
    test_id: Optional[str] = Field(
        default=None,
        description="Optional test identifier to mark derived graph entities as ephemeral test data",
    )
    acted_by: str = Field(
        default="actor",
        description="Identifier of the user or agent initiating the mock ingestion",
    )
    source: str = Field(
        default="file:mock",
        description="Ingestion stream source",
    )
    source_identity: Optional[str] = Field(
        default=None,
        description="Identity of stream sender (defaults to user:<acted_by>)",
    )
    destination_identity: str = Field(
        default="service:mock",
        description="Identity of receiving entity",
    )

    @field_validator("patient_id", mode="before")
    @classmethod
    def normalize_patient_id(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "MOCK_PATIENT"
        return v

    @field_validator("modality", mode="before")
    @classmethod
    def normalize_modality(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "MR"
        return v

    @field_validator("acted_by", mode="before")
    @classmethod
    def normalize_acted_by(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "actor"
        return v

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "file:mock"
        return v

    @field_validator("destination_identity", mode="before")
    @classmethod
    def normalize_destination_identity(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "service:mock"
        return v

    @model_validator(mode="after")
    def default_source_identity(self) -> "DicomMockIngestionMessage":
        if self.source_identity is None:
            self.source_identity = f"user:{self.acted_by}"
        return self


class FhirIngestionMessage(BaseModel):
    """Validated message payload for FhirActor resource fetch and graph registration."""

    patient_id: str = Field(
        default="",
        description="DICOM Patient ID associated with the study",
    )
    accession_number: Optional[str] = Field(
        default="",
        description="Study Accession Number",
    )
    study_uid: str = Field(
        default="",
        description="DICOM Study Instance UID",
    )
    series_uid: str = Field(
        default="",
        description="DICOM Series Instance UID",
    )
    test_id: Optional[str] = Field(
        default=None,
        description="Optional test identifier for ephemeral test data",
    )
    acted_by: str = Field(
        default="fhir_actor",
        description="Identifier of the user or agent initiating the FHIR operation",
    )
    group_id: Optional[str] = Field(
        default=None,
        description="Optional associated Group deterministic ID for ReBAC tenancy",
    )

    @field_validator("patient_id", "accession_number", "study_uid", "series_uid", mode="before")
    @classmethod
    def normalize_optional_uid_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return v

    @field_validator("acted_by", mode="before")
    @classmethod
    def normalize_acted_by(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "fhir_actor"
        return v


class EmbeddingMethod(StrEnum):
    """Supported vector embedding generation strategies dispatched by EmbeddingActor."""

    DIAGNOSTIC_REPORT = "diagnostic_report"


class EmbeddingMessage(BaseModel):
    """Validated message payload for EmbeddingActor vector computation and Neo4j registration."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: Literal["EMBED_RESOURCE"] = Field(
        default="EMBED_RESOURCE",
        description="Actor message type discriminator",
    )
    method: EmbeddingMethod = Field(
        default=EmbeddingMethod.DIAGNOSTIC_REPORT,
        description="Embedding generation method/strategy to invoke (e.g. 'diagnostic_report')",
    )
    resource: Any = Field(
        ...,
        description="Full FHIR R4B resource model (e.g. DiagnosticReport) or serialized resource dict",
    )
    group_context: Optional[Group] = Field(
        default=None,
        description="Optional resolved Group context for tenant-scoped LLM_Config lookup",
    )
    group_id: Optional[str] = Field(
        default=None,
        description="Optional Group deterministic ID used to resolve group_context when not supplied directly",
    )
    tag: str = Field(
        default=DEFAULT_EMBEDDING_TAG,
        description="LLM_Config purpose tag for embedding computation (default: 'embedding-v1')",
    )
    test_id: Optional[str] = Field(
        default=None,
        description="Optional test identifier to mark derived graph entities as ephemeral test data",
    )
    acted_by: str = Field(
        default="embedding_actor",
        description="Identifier of the user or agent initiating the embedding operation",
    )

    @field_validator("resource", mode="before")
    @classmethod
    def validate_resource_not_none(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("resource must not be None")
        return v

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return DEFAULT_EMBEDDING_TAG
        if isinstance(v, str):
            return v.strip()
        raise ValueError("tag must be a string")

    @field_validator("group_id", "test_id", mode="before")
    @classmethod
    def normalize_optional_str(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        return v

    @field_validator("acted_by", mode="before")
    @classmethod
    def normalize_acted_by(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "embedding_actor"
        if isinstance(v, str):
            return v.strip()
        return v

    @model_validator(mode="after")
    def sync_group_id_from_context(self) -> "EmbeddingMessage":
        if self.group_id is None and self.group_context is not None and self.group_context.id:
            self.group_id = self.group_context.id
        return self
