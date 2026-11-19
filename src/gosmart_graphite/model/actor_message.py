"""Pydantic message models for Pykka actor communication."""

from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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
