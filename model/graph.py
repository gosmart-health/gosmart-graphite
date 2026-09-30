import json
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

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


class DICOMConfigurationNode(AuditableNode):
    """Graph node representing a DICOM protocol configuration (DIMSE and DICOMweb)."""

    __label__: ClassVar[str] = "DICOMConfiguration"

    ae_title: str = Field(default="*", description="Application Entity Title (* for promiscuous SCP)")
    roles: List[str] = Field(default_factory=lambda: ["SCP"], description="Roles: SCP, SCU")
    host: str = Field(default="0.0.0.0", description="Host or bind address")
    port: int = Field(default=11112, description="TCP port")
    is_promiscuous: bool = Field(default=False, description="Promiscuous mode flag (True when ae_title is *)")
    tls_enabled: bool = Field(default=False, description="Whether TLS is enabled")
    tls_cert_path: Optional[str] = Field(default=None, description="Path to TLS certificate file")
    tls_key_path: Optional[str] = Field(default=None, description="Path to TLS private key file")
    tls_ca_path: Optional[str] = Field(default=None, description="Path to TLS CA bundle")
    tls_ciphers: Optional[List[str]] = Field(default=None, description="Allowed TLS ciphers")
    dicom_services: List[str] = Field(
        default_factory=lambda: ["*"],
        description="Supported DICOM services or SOP Classes (* accepts all)",
    )
    transfer_syntaxes: List[str] = Field(
        default_factory=lambda: ["*"],
        description="Proposed or acceptable transfer syntaxes (* accepts all)",
    )
    dicomweb_enabled: bool = Field(default=True, description="Enable DICOMweb support")
    dicomweb_url_prefix: Optional[str] = Field(default="/dicomweb", description="DICOMweb URL prefix")
    dicomweb_qido_enabled: bool = Field(default=True, description="Enable QIDO-RS")
    dicomweb_wado_enabled: bool = Field(default=True, description="Enable WADO-RS")
    dicomweb_stow_enabled: bool = Field(default=True, description="Enable STOW-RS")
    description: Optional[str] = Field(default=None, description="Human readable description")

    @model_validator(mode="before")
    @classmethod
    def compute_promiscuous_flag(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("ae_title") == "*":
            data.setdefault("is_promiscuous", True)
        return data

    @model_validator(mode="after")
    def sync_promiscuous_flag(self) -> "DICOMConfigurationNode":
        if self.ae_title == "*" and not self.is_promiscuous:
            object.__setattr__(self, "is_promiscuous", True)
        return self

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "ae_title": self.ae_title,
            "host": self.host,
            "port": self.port,
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


class HasDicomConfigurationRelation(AuditableRelation):
    """Relationship connecting System -> DICOMConfiguration."""

    __type__: ClassVar[str] = "HAS_DICOM_CONFIGURATION"


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
