from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Tuple, Type

from pydantic import AliasChoices, Field, model_validator

from model.graph import AuditableNode


class FHIRDomainResourceNode(AuditableNode):
    """Base graph node for all ingested FHIR R4B DomainResources.

    Inherits ``AuditableNode`` deterministic UUIDv5 ``id`` and audit fields while
    providing the FHIR base properties defined in ``docs/design-notes-informal/fhir_schema.md``:
    - ``uid``: Internal unique ID (typically ``ingestion-source:resource-id``)
    - ``resource_id``: Source FHIR system resource ID
    - ``status``: Lifecycle status of the resource
    - ``resource``: Inline raw FHIR JSON string
    - ``resourceUri``: External storage URI (``file://``, ``s3://``, ``gs://``)
    """

    __label__: ClassVar[str] = "FHIRDomainResource"

    # Properties excluded from Neo4j B-tree/range property indexing (e.g., raw JSON blobs)
    __non_indexed_fields__: ClassVar[Tuple[str, ...]] = ("resource", "textDiv", "presentedFormData")

    uid: str = Field(
        default="",
        description="Internal unique ID, typically 'ingestion-source:resource-id' or any idempotent string",
    )
    resource_id: str = Field(
        ...,
        validation_alias=AliasChoices("resource_id", "fhir_id"),
        description="The ID of the resource from the source FHIR system",
    )
    status: Optional[str] = Field(
        default=None,
        description="Resource status (e.g. 'active', 'cancelled', 'entered-in-error', 'final')",
    )
    resource: Optional[str] = Field(
        default=None,
        description="Inline raw FHIR JSON string",
    )
    resourceUri: Optional[str] = Field(
        default=None,
        description="URI to external file containing raw FHIR JSON (file://, s3://, gs://)",
    )

    @model_validator(mode="before")
    @classmethod
    def populate_default_uid(cls, data: Any) -> Any:
        if isinstance(data, dict):
            uid = data.get("uid")
            res_id = data.get("resource_id") or data.get("fhir_id")
            test_id = data.get("test_id")
            if isinstance(test_id, str):
                test_id = test_id.strip() or None
            if not uid and res_id:
                data["uid"] = f"fhir:{test_id}:{res_id}" if test_id else f"fhir:{res_id}"
        return data

    @model_validator(mode="after")
    def validate_resource_storage_invariants(self) -> "FHIRDomainResourceNode":
        if not self.uid and self.resource_id:
            suffix = f"{self.test_id}:{self.resource_id}" if self.test_id else self.resource_id
            object.__setattr__(self, "uid", f"fhir:{suffix}")

        if self.resource is None and self.resourceUri is None:
            raise ValueError("At least one of 'resource' or 'resourceUri' must be non-null on a FHIR node.")
        return self

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "uid": self.uid,
        }
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset


class FHIRPatientNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR Patient resource (:FHIR_Patient).

    Labeled ``FHIR_Patient`` to distinguish it from DICOM-derived ``:Patient`` nodes.
    """

    __label__: ClassVar[str] = "FHIR_Patient"

    identifiers: List[str] = Field(
        default_factory=list,
        description="List of Identifier Macros (system|value, |value, system|) correlated with DICOM Patient ID",
    )
    identifierType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for identifier types",
    )
    active: Optional[bool] = Field(
        default=None,
        description="Whether this patient's record is in active use",
    )
    name: Optional[List[str]] = Field(
        default=None,
        description="Formatted human-readable patient names",
    )
    gender: Optional[str] = Field(
        default=None,
        description="Administrative gender (male, female, other, unknown)",
    )
    birthDate: Optional[str] = Field(
        default=None,
        description="Date of birth (YYYY, YYYY-MM, or YYYY-MM-DD)",
    )
    managingOrganization: Optional[str] = Field(
        default=None,
        description="Reference Macro (reference|display) to the managing Organization",
    )


class EncounterNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR Encounter resource (:Encounter)."""

    __label__: ClassVar[str] = "Encounter"

    identifiers: List[str] = Field(
        default_factory=list,
        description="List of Identifier Macros",
    )
    class_: Optional[str] = Field(
        default=None,
        alias="class",
        validation_alias=AliasChoices("class", "class_"),
        serialization_alias="class",
        description="Coding Macro (system|code) representing classification of patient encounter",
    )
    type: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros for specific type of encounter",
    )
    subject: Optional[str] = Field(
        default=None,
        description="Reference Macro to the patient/subject",
    )
    participantType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for role of participant in the encounter",
    )
    participantPeriodStart: Optional[List[datetime]] = Field(
        default=None,
        description="List of participant period start timestamps",
    )
    participantPeriodEnd: Optional[List[datetime]] = Field(
        default=None,
        description="List of participant period end timestamps",
    )
    participantIndividual: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to persons involved in the encounter",
    )
    periodStart: Optional[datetime] = Field(
        default=None,
        description="Start time of the encounter period",
    )
    periodEnd: Optional[datetime] = Field(
        default=None,
        description="End time of the encounter period",
    )
    location: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to locations where the patient has been",
    )
    serviceProvider: Optional[str] = Field(
        default=None,
        description="Reference Macro to the custodian Organization",
    )


class ConditionNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR Condition resource (:Condition)."""

    __label__: ClassVar[str] = "Condition"

    identifiers: Optional[List[str]] = Field(
        default=None,
        description="List of Identifier Macros",
    )
    clinicalStatus: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros for active/recurrence/relapse/inactive/remission/resolved",
    )
    verificationStatus: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros for unconfirmed/provisional/differential/confirmed/refuted",
    )
    category: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros for problem-list-item or encounter-diagnosis",
    )
    code: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros identifying the condition, problem, or diagnosis",
    )
    subject: Optional[str] = Field(
        default=None,
        description="Reference Macro to the patient/subject",
    )
    encounter: Optional[str] = Field(
        default=None,
        description="Reference Macro to the Encounter during which this Condition was created",
    )
    onsetDateTime: Optional[datetime] = Field(
        default=None,
        description="Estimated or actual date-time of onset",
    )
    abatementDateTime: Optional[datetime] = Field(
        default=None,
        description="Date-time when condition resolved or went into remission",
    )
    recordedDate: Optional[datetime] = Field(
        default=None,
        description="Date record was first recorded",
    )


class ImagingStudyNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR ImagingStudy resource (:ImagingStudy)."""

    __label__: ClassVar[str] = "ImagingStudy"

    identifiers: List[str] = Field(
        default_factory=list,
        description="List of Identifier Macros (includes Accession Number and DICOM StudyInstanceUID)",
    )
    identifierType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for identifier types",
    )
    identifierUse: Optional[List[str]] = Field(
        default=None,
        description="List of Identifier.use field values",
    )
    subject: Optional[str] = Field(
        default=None,
        description="Reference Macro to the patient/subject",
    )
    encounter: Optional[str] = Field(
        default=None,
        description="Reference Macro to the originating Encounter",
    )
    basedOn: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to fulfilling ServiceRequest orders",
    )
    started: Optional[datetime] = Field(
        default=None,
        description="Date and time the study started",
    )
    procedureCode: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for the performed procedure codes",
    )
    procedureCodeText: Optional[str] = Field(
        default=None,
        description="Plain text description of the performed procedure",
    )
    location: Optional[str] = Field(
        default=None,
        description="Reference Macro to the Location where the study was performed",
    )
    studyUid: Optional[str] = Field(
        default=None,
        description="Extracted DICOM StudyInstanceUID (0020,000D) for direct linking to :Study",
    )
    seriesUid: Optional[List[str]] = Field(
        default=None,
        description="Extracted DICOM SeriesInstanceUID (0020,000E) values for linking to :Series",
    )
    instanceUid: Optional[List[str]] = Field(
        default=None,
        description="Extracted DICOM SOPInstanceUID (0008,0018) values for linking to :Instance",
    )
    seriesModality: List[str] = Field(
        default_factory=list,
        description="List of modality Coding Macros across series",
    )
    bodySite: Optional[List[str]] = Field(
        default=None,
        description="List of body site Coding Macros across series",
    )
    seriesStarted: Optional[List[datetime]] = Field(
        default=None,
        description="List of timestamps when each series was started",
    )


class LocationNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR Location resource (:Location)."""

    __label__: ClassVar[str] = "Location"

    identifiers: Optional[List[str]] = Field(
        default=None,
        description="List of Identifier Macros",
    )
    identifierType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for identifier types",
    )
    operationalStatus: Optional[str] = Field(
        default=None,
        description="Coding Macro for operational status (e.g. housekeeping/bed status)",
    )
    name: Optional[str] = Field(
        default=None,
        description="Name of the location as used by humans",
    )
    alias: Optional[List[str]] = Field(
        default=None,
        description="List of alternate names for the location",
    )
    description: Optional[str] = Field(
        default=None,
        description="Additional details about the location",
    )
    mode: Optional[str] = Field(
        default=None,
        description="Location mode: 'instance' or 'kind'",
    )
    type: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for type of function performed at the location",
    )
    physicalType: Optional[str] = Field(
        default=None,
        description="Coding Macro for physical form of the location (e.g. building, wing, room)",
    )
    telecom: Optional[List[str]] = Field(
        default=None,
        description="Contact details (system|value|use)",
    )
    address: Optional[str] = Field(
        default=None,
        description="Physical address string",
    )
    managingOrganization: Optional[str] = Field(
        default=None,
        description="Reference Macro to the Organization responsible for provisioning and upkeep",
    )
    partOf: Optional[str] = Field(
        default=None,
        description="Reference Macro to another Location this location is physically a part of",
    )


class ObservationNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR Observation resource (:Observation)."""

    __label__: ClassVar[str] = "Observation"

    identifiers: Optional[List[str]] = Field(
        default=None,
        description="List of Identifier Macros",
    )
    basedOn: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to ServiceRequest/orders",
    )
    encounter: Optional[str] = Field(
        default=None,
        description="Reference Macro to the originating Encounter",
    )
    code: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros describing what was observed",
    )
    subject: Optional[str] = Field(
        default=None,
        description="Reference Macro to the patient/subject",
    )
    performer: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to who is responsible for the observation",
    )
    codes: Optional[List[str]] = Field(
        default=None,
        description="Coded Value Macro: parallel array of Coding Macros synchronized with numericValues and units",
    )
    numericValues: Optional[List[float]] = Field(
        default=None,
        description="Coded Value Macro: parallel array of numeric values synchronized with codes and units",
    )
    units: Optional[List[str]] = Field(
        default=None,
        description="Coded Value Macro: parallel array of unit Coding Macros synchronized with codes and numericValues",
    )
    bodySite: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for observed body part",
    )
    text: Optional[str] = Field(
        default=None,
        description="Text summary or string value of the observation",
    )

    @model_validator(mode="after")
    def validate_coded_value_arrays(self) -> "ObservationNode":
        provided = [x for x in (self.codes, self.numericValues, self.units) if x is not None]
        if provided:
            lengths = {len(x) for x in provided}
            if len(provided) != 3 or len(lengths) > 1:
                raise ValueError(
                    "ObservationNode 'codes', 'numericValues', and 'units' must all be provided "
                    "with identical array lengths when using the Coded Value Macro."
                )
        return self


class OrganizationNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR Organization resource (:Organization)."""

    __label__: ClassVar[str] = "Organization"

    identifiers: Optional[List[str]] = Field(
        default=None,
        description="List of Identifier Macros (e.g. NPI, facility IDs, OIDs)",
    )
    identifierType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for identifier types",
    )
    active: Optional[bool] = Field(
        default=None,
        description="Whether the organization's record is still in active use",
    )
    type: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for kind of organization",
    )
    name: Optional[str] = Field(
        default=None,
        description="Name used for the organization",
    )
    alias: Optional[List[str]] = Field(
        default=None,
        description="List of alternate names for the organization",
    )
    telecom: Optional[List[str]] = Field(
        default=None,
        description="Contact details (system|value|use)",
    )
    address: Optional[List[str]] = Field(
        default=None,
        description="Addresses for the organization",
    )
    partOf: Optional[str] = Field(
        default=None,
        description="Reference Macro to the parent Organization of which this organization forms a part",
    )


class DiagnosticReportNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR DiagnosticReport resource (:DiagnosticReport)."""

    __label__: ClassVar[str] = "DiagnosticReport"

    identifiers: Optional[List[str]] = Field(
        default=None,
        description="List of Identifier Macros (may include Accession Number ACSN)",
    )
    identifierType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for identifier types",
    )
    basedOn: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to ServiceRequest orders",
    )
    imagingStudy: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to ImagingStudy resources",
    )
    category: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for service category",
    )
    code: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros for the diagnostic report name/code",
    )
    subject: Optional[str] = Field(
        default=None,
        description="Reference Macro to the patient/subject",
    )
    encounter: Optional[str] = Field(
        default=None,
        description="Reference Macro to the healthcare event when the order was made",
    )
    effectiveDateTime: Optional[datetime] = Field(
        default=None,
        description="Clinically relevant time/time-period for the report",
    )
    issued: Optional[datetime] = Field(
        default=None,
        description="DateTime this version was made available",
    )
    performer: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to the responsible Diagnostic Service",
    )
    result: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to supporting Observation resources",
    )
    codes: Optional[List[str]] = Field(
        default=None,
        description="Coded Value Macro: parallel array of Coding Macros synchronized with numericValues and units",
    )
    numericValues: Optional[List[float]] = Field(
        default=None,
        description="Coded Value Macro: parallel array of numeric values synchronized with codes and units",
    )
    units: Optional[List[str]] = Field(
        default=None,
        description="Coded Value Macro: parallel array of unit Coding Macros synchronized with codes and numericValues",
    )
    conclusion: Optional[str] = Field(
        default=None,
        description="Clinical conclusion (interpretation) of test results",
    )
    textStatus: Optional[str] = Field(
        default=None,
        description="Narrative text status (e.g. generated, extensions, additional, empty)",
    )
    textDiv: Optional[str] = Field(
        default=None,
        description="XHTML narrative div content",
    )
    presentedFormData: Optional[List[str]] = Field(
        default=None,
        description="Base64-encoded attachment data when present",
    )

    @model_validator(mode="after")
    def validate_coded_value_arrays(self) -> "DiagnosticReportNode":
        provided = [x for x in (self.codes, self.numericValues, self.units) if x is not None]
        if provided:
            lengths = {len(x) for x in provided}
            if len(provided) != 3 or len(lengths) > 1:
                raise ValueError(
                    "DiagnosticReportNode 'codes', 'numericValues', and 'units' must all be provided "
                    "with identical array lengths when using the Coded Value Macro."
                )
        return self


class PractitionerNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR Practitioner resource (:Practitioner)."""

    __label__: ClassVar[str] = "Practitioner"

    identifiers: Optional[List[str]] = Field(
        default=None,
        description="List of Identifier Macros (e.g. NPI, state license, internal provider ID)",
    )
    identifierType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for identifier types",
    )
    active: Optional[bool] = Field(
        default=None,
        description="Whether this practitioner's record is in active use",
    )
    name: Optional[List[str]] = Field(
        default=None,
        description="Formatted human-readable names (e.g. 'Dr. Sarah Lin, MD')",
    )
    telecom: Optional[List[str]] = Field(
        default=None,
        description="Contact details (system|value|use)",
    )
    address: Optional[List[str]] = Field(
        default=None,
        description="Addresses of the practitioner",
    )
    gender: Optional[str] = Field(
        default=None,
        description="Administrative gender (male, female, other, unknown)",
    )
    birthDate: Optional[str] = Field(
        default=None,
        description="Date of birth",
    )
    qualificationCode: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros flattened from qualification[].code",
    )
    qualificationIssuer: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros flattened from qualification[].issuer",
    )
    communication: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for languages the practitioner can use in patient communication",
    )
    organization: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to associated Organizations",
    )
    specialty: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for clinical specialty",
    )


class ServiceRequestNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR ServiceRequest resource (:ServiceRequest)."""

    __label__: ClassVar[str] = "ServiceRequest"

    identifiers: List[str] = Field(
        default_factory=list,
        description="List of Identifier Macros (includes Accession Number ACSN and Placer Order PLAC)",
    )
    identifierType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for identifier types",
    )
    intent: str = Field(
        default="order",
        description=(
            "Intent: proposal, plan, directive, order, original-order, "
            "reflex-order, filler-order, instance-order, option"
        ),
    )
    category: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for classification of service",
    )
    priority: Optional[str] = Field(
        default=None,
        description="Urgency: routine, urgent, asap, stat",
    )
    code: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros for what is being requested/ordered",
    )
    codeText: Optional[str] = Field(
        default=None,
        description="Plain text description of the requested service",
    )
    subject: Optional[str] = Field(
        default=None,
        description="Reference Macro to the patient/subject",
    )
    encounter: Optional[str] = Field(
        default=None,
        description="Reference Macro to the Encounter in which the request was created",
    )
    occurrenceDateTime: Optional[datetime] = Field(
        default=None,
        description="When the service should occur",
    )
    authoredOn: Optional[datetime] = Field(
        default=None,
        description="Date request was signed/authored",
    )
    requester: Optional[str] = Field(
        default=None,
        description="Reference Macro to the requesting Practitioner/Organization",
    )
    reasonCode: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros explaining why the service was ordered",
    )
    bodySite: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for anatomical location on body",
    )
    bodySiteText: Optional[str] = Field(
        default=None,
        description="Plain text description of anatomical location",
    )
    patientInstruction: Optional[str] = Field(
        default=None,
        description="Patient or consumer-oriented instructions",
    )
    supportingInfo: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to additional clinical information",
    )


class ProcedureNode(FHIRDomainResourceNode):
    """Graph node representing a FHIR Procedure resource (:Procedure)."""

    __label__: ClassVar[str] = "Procedure"

    identifiers: Optional[List[str]] = Field(
        default=None,
        description="List of Identifier Macros",
    )
    identifierType: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for identifier types",
    )
    basedOn: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to ServiceRequest orders for this procedure",
    )
    partOf: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to larger procedures/observations/medication administrations",
    )
    category: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for classification of the procedure",
    )
    code: List[str] = Field(
        default_factory=list,
        description="List of Coding Macros identifying the procedure",
    )
    subject: Optional[str] = Field(
        default=None,
        description="Reference Macro to the patient/subject",
    )
    encounter: Optional[str] = Field(
        default=None,
        description="Reference Macro to the Encounter associated with the procedure",
    )
    performedDateTime: Optional[datetime] = Field(
        default=None,
        description="When the procedure was performed",
    )
    recorder: Optional[str] = Field(
        default=None,
        description="Reference Macro to who recorded the procedure",
    )
    asserter: Optional[str] = Field(
        default=None,
        description="Reference Macro to person who asserts this procedure",
    )
    performer: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to practitioners who performed the procedure",
    )
    location: Optional[str] = Field(
        default=None,
        description="Reference Macro to Location where the procedure occurred",
    )
    reasonCode: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for coded reason procedure was performed",
    )
    bodySite: Optional[List[str]] = Field(
        default=None,
        description="List of Coding Macros for target body sites",
    )
    report: Optional[List[str]] = Field(
        default=None,
        description="List of Reference Macros to DiagnosticReport resulting from this procedure",
    )


FHIR_NODE_CLASSES: Tuple[Type[FHIRDomainResourceNode], ...] = (
    FHIRPatientNode,
    EncounterNode,
    ConditionNode,
    ImagingStudyNode,
    LocationNode,
    ObservationNode,
    OrganizationNode,
    DiagnosticReportNode,
    PractitionerNode,
    ServiceRequestNode,
    ProcedureNode,
)

FHIR_NODE_LABELS: Tuple[str, ...] = tuple(cls.__label__ for cls in FHIR_NODE_CLASSES)
