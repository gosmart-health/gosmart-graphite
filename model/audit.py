import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, ClassVar, Dict, Literal, Optional

from pydantic import Field, field_validator, model_validator

from core.config import get_settings
from model.graph import AuditableNode

AuditSeverity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]


class AuditSeverityEnum(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AuditAction(str, Enum):
    """Enumerated standard human-readable audit actions following the Object + Verb convention."""

    # User & Identity Actions
    USER_CREATED = "User Created"
    USER_CHANGED = "User Changed"
    USER_DISABLED = "User Disabled"
    USER_ENABLED = "User Enabled"
    USER_DELETED = "User Deleted"
    USER_AUTHENTICATED = "User Authenticated"
    USER_LOGIN_FAILED = "User Login Failed"
    USER_PASSWORD_RESET = "User Password Reset"
    ADMIN_BOOTSTRAPPED = "Admin Bootstrapped"

    # Group & Access Actions
    GROUP_CREATED = "Group Created"
    GROUP_CHANGED = "Group Changed"
    GROUP_DELETED = "Group Deleted"
    GROUP_MEMBER_ADDED = "Group Member Added"
    GROUP_MEMBER_REMOVED = "Group Member Removed"
    STUDY_ASSOCIATED_TO_GROUP = "Study Associated with Group"
    STUDY_REASSOCIATED_TO_GROUP = "Study Reassociated with Group"
    PATIENT_ASSOCIATED_TO_GROUP = "Patient Associated with Group"

    # DICOM Configuration Actions
    DICOM_CONFIGURATION_CREATED = "DICOM Configuration Created"
    DICOM_CONFIGURATION_CHANGED = "DICOM Configuration Changed"
    DICOM_CONFIGURATION_REMOVED = "DICOM Configuration Removed"
    DICOM_CONFIGURATION_SERVES_GROUP = "DICOM Configuration Associated with Group"
    DICOM_CONFIGURATION_DISSOCIATED = "DICOM Configuration Dissociated from Group"

    # DICOM Protocol / DIMSE Actions
    DICOM_ASSOCIATION_ESTABLISHED = "DICOM Association Established"
    DICOM_ASSOCIATION_REJECTED = "DICOM Association Rejected"
    DICOM_ASSOCIATION_RELEASED = "DICOM Association Released"
    DICOM_C_STORE_INGESTED = "DICOM C-STORE Ingested"
    DICOM_C_STORE_SENT = "DICOM C-STORE Sent"
    DICOM_C_STORE_FAILED = "DICOM C-STORE Failed"
    DICOM_C_ECHO_VERIFIED = "DICOM C-ECHO Verified"
    DICOM_C_FIND_EXECUTED = "DICOM C-FIND Executed"
    DICOM_C_MOVE_REQUESTED = "DICOM C-MOVE Requested"
    DICOM_C_GET_EXECUTED = "DICOM C-GET Executed"

    # Ingestion & Clinical Data Actions
    STUDY_INGESTED = "Study Ingested"
    STUDY_DELETED = "Study Deleted"
    SERIES_INGESTED = "Series Ingested"
    INSTANCE_INGESTED = "Instance Ingested"
    INSTANCE_DELETED = "Instance Deleted"
    DATASET_MOCK_INGESTED = "Dataset Mock Ingested"

    # DICOMweb (PS 3.18) Actions
    DICOMWEB_STUDIES_QUERIED = "DICOMweb Studies Queried"
    DICOMWEB_SERIES_QUERIED = "DICOMweb Series Queried"
    DICOMWEB_INSTANCES_QUERIED = "DICOMweb Instances Queried"
    DICOMWEB_INSTANCE_RETRIEVED = "DICOMweb Instance Retrieved"
    DICOMWEB_SERIES_RETRIEVED = "DICOMweb Series Retrieved"
    DICOMWEB_STUDY_RETRIEVED = "DICOMweb Study Retrieved"
    DICOMWEB_INSTANCES_STORED = "DICOMweb Instances Stored"
    DICOMWEB_WADO_URI_RETRIEVED = "DICOMweb WADO-URI Retrieved"

    # System & Media Operations
    SYSTEM_BOOTSTRAPPED = "System Bootstrapped"
    STORAGE_FILE_WRITTEN = "Storage File Written"
    STORAGE_FILE_DELETED = "Storage File Deleted"


class AuditNode(AuditableNode):
    """Auditable graph node capturing a structured audit tracking event."""

    __label__: ClassVar[str] = "AuditLog"

    action: str = Field(
        ...,
        description="Human readable action string formatted as Object + Verb (e.g. 'User Disabled')",
    )
    statusCode: int = Field(
        default=200,
        description="Code aligned with HTTP status codes (200=success, 400=bad request, 500=error)",
    )
    statusMessage: str = Field(
        default="OK",
        description="Arbitrary status message",
    )
    severity: AuditSeverity = Field(
        default="INFO",
        description="Severity level: INFO, WARNING, ERROR, CRITICAL",
    )
    actorId: str = Field(
        ...,
        description="Structured actor identity: Label:id (e.g. 'user:admin', 'ae:MDICOM', 'system:gosmart')",
    )
    actor: str = Field(
        ...,
        description="Human-readable actor name or email (e.g. 'admin@hospital.org', 'MDICOM')",
    )
    targetId: Optional[str] = Field(
        default=None,
        description="Optional structured target identity: Label:id (e.g. 'study:1.2.392...', 'user:123')",
    )
    target: Optional[str] = Field(
        default=None,
        description="Optional human-readable target (e.g. 'Study 1.2.392...', 'admin@gosmart.health')",
    )
    result: str = Field(
        default="",
        description="Human readable string describing the result",
    )
    details: Optional[str] = Field(
        default=None,
        description="Optional JSON string containing machine-readable details about the action",
    )
    event_id: Optional[str] = Field(
        default=None,
        description="Optional explicit event unique identifier used for deterministic ID derivation",
    )

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, v: Any) -> str:
        if isinstance(v, AuditAction):
            return v.value
        if isinstance(v, str):
            return v.strip()
        return str(v)

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, v: Any) -> str:
        if isinstance(v, str):
            norm = v.strip().upper()
            if norm in ("INFO", "WARNING", "ERROR", "CRITICAL"):
                return norm
        elif isinstance(v, AuditSeverityEnum):
            return v.value
        return "INFO"

    @field_validator("details", mode="before")
    @classmethod
    def normalize_details(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, (dict, list)):
            return json.dumps(v, sort_keys=True, default=str)
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return str(v)

    @model_validator(mode="before")
    @classmethod
    def set_default_timestamps(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("CreatedAt"):
                data["CreatedAt"] = datetime.now(timezone.utc)
            if not data.get("ActedAt"):
                data["ActedAt"] = data["CreatedAt"]
            if not data.get("ExpiresAt"):
                settings = get_settings()
                retention_days = getattr(settings, "AUDIT_RETENTION_DAYS", 365)
                base_time = data.get("CreatedAt")
                if isinstance(base_time, str):
                    try:
                        base_time = datetime.fromisoformat(base_time)
                    except Exception:
                        base_time = datetime.now(timezone.utc)
                data["ExpiresAt"] = base_time + timedelta(days=retention_days)
        return data

    def get_identity_subset(self) -> Dict[str, Any]:
        subset: Dict[str, Any] = {
            "entity": self.__label__,
            "action": self.action,
            "actorId": self.actorId,
            "targetId": self.targetId or "",
            "created_at": self.CreatedAt.isoformat() if self.CreatedAt else "",
        }
        if self.event_id:
            subset["event_id"] = self.event_id
        if self.test_id:
            subset["test_id"] = self.test_id
        return subset
