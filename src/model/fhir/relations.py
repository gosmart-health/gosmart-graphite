from typing import ClassVar, Tuple, Type

from model.graph import AuditableRelation


class SubjectRelation(AuditableRelation):
    """Relationship (:SUBJECT) connecting a clinical resource to its FHIR_Patient subject."""

    __type__: ClassVar[str] = "SUBJECT"


class PerformedInRelation(AuditableRelation):
    """Relationship (:PERFORMED_IN) connecting ImagingStudy -> Encounter."""

    __type__: ClassVar[str] = "PERFORMED_IN"


class BasedOnRelation(AuditableRelation):
    """Relationship (:BASED_ON) connecting ImagingStudy / DiagnosticReport / Procedure -> ServiceRequest."""

    __type__: ClassVar[str] = "BASED_ON"


class BasedOnStudyRelation(AuditableRelation):
    """Relationship (:BASED_ON_STUDY) connecting DiagnosticReport -> ImagingStudy."""

    __type__: ClassVar[str] = "BASED_ON_STUDY"


class SupportsReportRelation(AuditableRelation):
    """Relationship (:SUPPORTS_REPORT) connecting Observation -> DiagnosticReport."""

    __type__: ClassVar[str] = "SUPPORTS_REPORT"


class HasEncounterRelation(AuditableRelation):
    """Relationship (:HAS_ENCOUNTER) connecting Condition -> Encounter."""

    __type__: ClassVar[str] = "HAS_ENCOUNTER"


class RequestsRelation(AuditableRelation):
    """Relationship (:REQUESTS) connecting Practitioner -> Procedure / ServiceRequest."""

    __type__: ClassVar[str] = "REQUESTS"


class PerformsRelation(AuditableRelation):
    """Relationship (:PERFORMS) connecting Practitioner -> Procedure."""

    __type__: ClassVar[str] = "PERFORMS"


class FhirPerformedAtRelation(AuditableRelation):
    """Relationship (:PERFORMED_AT) connecting ImagingStudy -> Location."""

    __type__: ClassVar[str] = "PERFORMED_AT"


class ContainsRelation(AuditableRelation):
    """Relationship (:CONTAINS) connecting ServiceRequest -> Practitioner or parent resource -> sub-resource."""

    __type__: ClassVar[str] = "CONTAINS"


class PartOfRelation(AuditableRelation):
    """Relationship (:PART_OF) connecting Location -> Organization or child -> parent Organization/Location."""

    __type__: ClassVar[str] = "PART_OF"


class FhirBelongsToRelation(AuditableRelation):
    """Relationship (:BELONGS_TO) connecting Practitioner -> Organization."""

    __type__: ClassVar[str] = "BELONGS_TO"


class CreatesRelation(AuditableRelation):
    """Relationship (:CREATES) connecting Practitioner -> DiagnosticReport."""

    __type__: ClassVar[str] = "CREATES"


# Cross-Domain (DICOM <-> FHIR) Relationships


class SameAsRelation(AuditableRelation):
    """Relationship (:SAME_AS) connecting DICOM Patient -> FHIR_Patient via Patient ID / MRN."""

    __type__: ClassVar[str] = "SAME_AS"


class ReferencesStudyRelation(AuditableRelation):
    """Relationship (:REFERENCES_STUDY) connecting FHIR ImagingStudy -> DICOM Study via StudyInstanceUID."""

    __type__: ClassVar[str] = "REFERENCES_STUDY"


class ReferencesSeriesRelation(AuditableRelation):
    """Relationship (:REFERENCES_SERIES) connecting FHIR ImagingStudy -> DICOM Series via SeriesInstanceUID."""

    __type__: ClassVar[str] = "REFERENCES_SERIES"


class ReferencesInstanceRelation(AuditableRelation):
    """Relationship (:REFERENCES_INSTANCE) connecting FHIR ImagingStudy -> DICOM Instance via SOPInstanceUID."""

    __type__: ClassVar[str] = "REFERENCES_INSTANCE"


class FulfilledByRelation(AuditableRelation):
    """Relationship (:FULFILLED_BY) connecting FHIR ServiceRequest -> DICOM Study via Accession Number."""

    __type__: ClassVar[str] = "FULFILLED_BY"


FHIR_RELATION_CLASSES: Tuple[Type[AuditableRelation], ...] = (
    SubjectRelation,
    PerformedInRelation,
    BasedOnRelation,
    BasedOnStudyRelation,
    SupportsReportRelation,
    HasEncounterRelation,
    RequestsRelation,
    PerformsRelation,
    FhirPerformedAtRelation,
    ContainsRelation,
    PartOfRelation,
    FhirBelongsToRelation,
    CreatesRelation,
    SameAsRelation,
    ReferencesStudyRelation,
    ReferencesSeriesRelation,
    ReferencesInstanceRelation,
    FulfilledByRelation,
)
