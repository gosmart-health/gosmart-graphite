from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, Field, model_validator

from gosmart_graphite.model.auditable import AuditableNode, AuditableRelation

# Concrete DICOM Graph Nodes


class PatientNode(AuditableNode):
    """Graph node representing a DICOM Patient."""

    __label__: ClassVar[str] = "Patient"

    patient_id: str = Field(..., description="DICOM Patient ID (0010,0020)")
    patient_name: Optional[str] = Field(None, description="Patient Name (0010,0010)")
    patient_birth_date: Optional[str] = Field(None, description="Patient Birth Date (0010,0030)")
    patient_sex: Optional[str] = Field(None, description="Patient Sex (0010,0040)")

    def get_identity_subset(self) -> Dict[str, Any]:
        return {"entity": self.__label__, "patient_id": self.patient_id}


class StudyNode(AuditableNode):
    """Graph node representing a DICOM Study."""

    __label__: ClassVar[str] = "Study"

    study_instance_uid: str = Field(..., description="Study Instance UID (0020,000D)")
    study_date: Optional[str] = Field(None, description="Study Date (0008,0020)")
    study_time: Optional[str] = Field(None, description="Study Time (0008,0030)")
    study_description: Optional[str] = Field(None, description="Study Description (0008,1030)")
    accession_number: Optional[str] = Field(None, description="Accession Number (0008,0050)")

    def get_identity_subset(self) -> Dict[str, Any]:
        return {"entity": self.__label__, "study_instance_uid": self.study_instance_uid}


class SeriesNode(AuditableNode):
    """Graph node representing a DICOM Series."""

    __label__: ClassVar[str] = "Series"

    series_instance_uid: str = Field(..., description="Series Instance UID (0020,000E)")
    series_number: Optional[int] = Field(None, description="Series Number (0020,0011)")
    modality: str = Field("OT", description="Modality (0008,0060), e.g. CT, MR, US")
    series_description: Optional[str] = Field(None, description="Series Description (0008,103E)")
    body_part_examined: Optional[str] = Field(None, description="Body Part Examined (0018,0015)")

    def get_identity_subset(self) -> Dict[str, Any]:
        return {"entity": self.__label__, "series_instance_uid": self.series_instance_uid}


class InstanceNode(AuditableNode):
    """Graph node representing a DICOM SOP Instance."""

    __label__: ClassVar[str] = "Instance"

    sop_instance_uid: str = Field(..., description="SOP Instance UID (0008,0018)")
    sop_class_uid: Optional[str] = Field(None, description="SOP Class UID (0008,0016)")
    instance_number: Optional[int] = Field(None, description="Instance Number (0020,0013)")
    transfer_syntax_uid: Optional[str] = Field(None, description="Transfer Syntax UID (0002,0010)")
    storage_uri: Optional[str] = Field(None, description="URI where the raw DICOM file is persisted")
    file_size_bytes: Optional[int] = Field(None, description="File size in bytes")

    # Instance-level stream provenance
    Source: Optional[str] = Field(
        default=None,
        description="Ingestion stream source: dimse:<ip>, stow:<ip>, file:<path>",
    )
    SourceIdentity: Optional[str] = Field(
        default=None,
        description="Identity of stream sender: ae:<AE_title> or user:<user_id>",
    )
    Destination: Optional[str] = Field(
        default=None,
        description="Local destination endpoint: dimse:<ip>:<port>, stow:<host>:<port>, file:<uri>",
    )
    DestinationIdentity: Optional[str] = Field(
        default=None,
        description="Identity of receiving entity: ae:<called_aet> or service:<name>",
    )

    def get_identity_subset(self) -> Dict[str, Any]:
        return {"entity": self.__label__, "sop_instance_uid": self.sop_instance_uid}


class SystemNode(AuditableNode):
    """Graph node representing the immutable root System."""

    __label__: ClassVar[str] = "System"

    name: str = Field(default="GoSmart Health VNA", description="Name of the system")
    owner: str = Field(default="GoSmart Health", description="Name of the owner user or organization")
    address: str = Field(default="Half Moon Bay, California", description="Physical address string")
    email: str = Field(default="admin@gosmart.health", description="Email address of the owner (non-auth)")
    phone: str = Field(default="+1 (650) 535-0097", description="Phone number of the owner (non-auth)")
    serial_number: str = Field(default="GSH-VNA-0001", description="System serial number")
    installed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Installation UTC DateTime",
    )

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {"entity": self.__label__}
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset


class FHIRConfigurationNode(AuditableNode):
    """Graph node representing a FHIR endpoint configuration."""

    __label__: ClassVar[str] = "FHIRConfiguration"

    accession_system: str = Field(
        default="",
        description="The accession system of the FHIR configuration.",
    )
    patient_id_system: str = Field(
        default="",
        description="The patient ID system of the FHIR configuration.",
    )
    url: str = Field(
        default="",
        description="The URL of the FHIR configuration.",
    )
    poll_sec: int = Field(
        default=120,
        description="The number of seconds between polls of the FHIR configuration.",
    )

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "url": self.url,
        }
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset


class TransferStatusNode(AuditableNode):
    """Auditable database record capturing an individual DICOM DIMSE transfer event.

    Recorded for asynchronous transfer tracking, Store SCU transfers, and C-STORE SCP receipts.
    Note: Maintained as a standalone node in Neo4j without edges to Instance nodes to avoid
    dense supernode link explosions.
    """

    __label__: ClassVar[str] = "TransferStatus"

    sender: str = Field(..., description="Sender AE Title (e.g. GOSMART_GRAPHITE or remote SCU)")
    recipient: str = Field(
        ...,
        validation_alias=AliasChoices("recipient", "recepient"),
        description="Recipient AE Title (e.g. MDICOM or GOSMART_GRAPHITE)",
    )
    objectRef: str = Field(..., description="The URI of the raw DICOM object")
    transferTimeMs: int = Field(default=0, description="Transfer duration in milliseconds")
    transferBytes: int = Field(default=0, description="Number of bytes transferred")
    statusCode: str = Field(default="0x0000", description="DICOM DIMSE status code (e.g. 0x0000, 0xA700)")
    statusMessage: Optional[str] = Field(default=None, description="Human-readable status or error message")

    # Additional provenance fields
    sop_instance_uid: Optional[str] = Field(default=None, description="Affected SOP Instance UID")
    direction: str = Field(default="OUTBOUND", description="Transfer direction: OUTBOUND (SCU) or INBOUND (SCP)")
    service_type: str = Field(default="C-STORE", description="DIMSE service: C-STORE, C-MOVE, etc.")

    @model_validator(mode="after")
    def set_default_expiration(self) -> "TransferStatusNode":
        """Set ExpiresAt to 30 days from creation date if not explicitly set."""
        if self.ExpiresAt is None:
            base_time = self.CreatedAt or datetime.now(timezone.utc)
            self.ExpiresAt = base_time + timedelta(days=30)
        return self

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "sender": self.sender,
            "recipient": self.recipient,
            "objectRef": self.objectRef,
            "created_at": self.CreatedAt.isoformat() if self.CreatedAt else "",
        }
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset


# Concrete DICOM Graph Relationships


class HasStudyRelation(AuditableRelation):
    """Relationship connecting Patient -> Study."""

    __type__: ClassVar[str] = "HAS_STUDY"


class HasSeriesRelation(AuditableRelation):
    """Relationship connecting Study -> Series."""

    __type__: ClassVar[str] = "HAS_SERIES"


class HasInstanceRelation(AuditableRelation):
    """Relationship connecting Series -> Instance."""

    __type__: ClassVar[str] = "HAS_INSTANCE"


# Generic Query Models


class GraphNode(BaseModel):
    id: Any
    labels: List[str]
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    id: Any
    type: str
    start_node_id: Any
    end_node_id: Any
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphQueryResult(BaseModel):
    nodes: List[GraphNode] = Field(default_factory=list)
    relationships: List[GraphRelationship] = Field(default_factory=list)
    raw_records: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class CypherQueryRequest(BaseModel):
    query: str = Field(..., description="Cypher query to execute")
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Parameters for the Cypher query")
