"""Semantic vector search result and opt-in context label models."""

from __future__ import annotations

from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, Field

from gosmart_graphite.model.fhir import EncounterNode, FHIRPatientNode, ImagingStudyNode
from gosmart_graphite.model.graph import PatientNode, StudyNode


class SearchIncludeLabel(StrEnum):
    """Supported related node labels that can be hydrated onto a SearchResult."""

    ENCOUNTER = "Encounter"
    FHIR_PATIENT = "FHIR_Patient"
    IMAGING_STUDY = "ImagingStudy"
    PATIENT = "Patient"
    STUDY = "Study"


class SearchResult(BaseModel):
    """Deduplicated semantic search hit pointing to a matched domain graph node."""

    score: float = Field(..., description="Vector similarity score in [0.0, 1.0]")
    node_label: str = Field(..., description="Primary Neo4j label of the matched target node")
    node_id: str = Field(..., description="Deterministic UUIDv5 id of the matched target node")
    text: str = Field(..., description="Source text stored on the highest-scoring matched :Embedding node")
    target_field_tag: Optional[str] = Field(
        default=None,
        description="Field/aspect tag of the winning :Embedding node (e.g. 'FINDINGS', 'CONCLUSION')",
    )
    model_tag: Optional[str] = Field(
        default=None,
        description="Embedding modelTag used to produce the matched vector",
    )
    embedding_id: Optional[str] = Field(
        default=None,
        description="Deterministic id of the winning :Embedding node",
    )
    is_deleted: bool = Field(
        default=False,
        description="True when the matched target node, :Embedding node, or :EMBEDS relationship is soft-deleted",
    )

    # Opt-in related clinical & DICOM context nodes (populated only when requested via `include`)
    encounter: Optional[EncounterNode] = Field(
        default=None,
        description="Associated :Encounter node when SearchIncludeLabel.ENCOUNTER is requested",
    )
    fhir_patient: Optional[FHIRPatientNode] = Field(
        default=None,
        description="Associated :FHIR_Patient node when SearchIncludeLabel.FHIR_PATIENT is requested",
    )
    imaging_studies: Optional[List[ImagingStudyNode]] = Field(
        default=None,
        description="Associated :ImagingStudy nodes when SearchIncludeLabel.IMAGING_STUDY is requested",
    )
    patient: Optional[PatientNode] = Field(
        default=None,
        description="Associated DICOM :Patient node when SearchIncludeLabel.PATIENT is requested",
    )
    studies: Optional[List[StudyNode]] = Field(
        default=None,
        description="Associated DICOM :Study nodes when SearchIncludeLabel.STUDY is requested",
    )


__all__ = [
    "SearchIncludeLabel",
    "SearchResult",
]
