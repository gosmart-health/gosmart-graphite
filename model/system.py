from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class DICOMConfigurationResponse(BaseModel):
    """API response model for a DICOM Configuration."""

    id: str = Field(..., description="Deterministic configuration ID")
    ae_title: str = Field(..., description="Application Entity Title (* for promiscuous mode)")
    roles: List[str] = Field(default_factory=list, description="Roles: SCP, SCU")
    host: str = Field(..., description="Host or bind address")
    port: int = Field(..., description="TCP port")
    is_local: bool = Field(default=False, description="Whether this configuration represents a local server")
    is_promiscuous: bool = Field(default=False, description="Promiscuous mode flag")
    tls_enabled: bool = Field(default=False, description="Whether TLS is enabled")
    tls_cert_path: Optional[str] = Field(default=None, description="Path to TLS certificate file")
    tls_key_path: Optional[str] = Field(default=None, description="Path to TLS private key file")
    tls_ca_path: Optional[str] = Field(default=None, description="Path to TLS CA bundle")
    dicom_services: List[str] = Field(default_factory=lambda: ["*"], description="Supported DICOM services/SOP Classes")
    transfer_syntaxes: List[str] = Field(
        default_factory=lambda: ["*"], description="Proposed/acceptable transfer syntaxes"
    )
    dicomweb_enabled: bool = Field(default=True, description="Enable DICOMweb support")
    dicomweb_url_prefix: Optional[str] = Field(default="/dicomweb", description="DICOMweb URL prefix")
    dicomweb_qido_enabled: bool = Field(default=True, description="Enable QIDO-RS")
    dicomweb_wado_enabled: bool = Field(default=True, description="Enable WADO-RS")
    dicomweb_stow_enabled: bool = Field(default=True, description="Enable STOW-RS")
    description: Optional[str] = Field(default=None, description="Human readable description")
    test_id: Optional[str] = Field(default=None, description="Test identifier if ephemeral")
    action: Optional[str] = Field(default="CREATE", description="Auditable action state")
    created_at: Optional[datetime] = Field(default=None, description="Created timestamp (UTC)")
    updated_at: Optional[datetime] = Field(default=None, description="Updated timestamp (UTC)")

    model_config = {"populate_by_name": True}


class DICOMConfigurationCreateRequest(BaseModel):
    """API request model to create a new DICOM Configuration."""

    ae_title: str = Field(default="*", description="Application Entity Title (* for promiscuous mode)")
    roles: List[str] = Field(default_factory=lambda: ["SCP"], description="Roles: SCP, SCU")
    host: str = Field(default="0.0.0.0", description="Host or bind address")
    port: int = Field(default=11112, description="TCP port")
    is_local: bool = Field(
        default=False,
        description="Whether this is a local server (True) bound to a local interface or remote SCP (False)",
    )
    tls_enabled: bool = Field(default=False, description="Whether TLS is enabled")
    tls_cert_path: Optional[str] = Field(default=None, description="Path to TLS certificate file")
    tls_key_path: Optional[str] = Field(default=None, description="Path to TLS private key file")
    tls_ca_path: Optional[str] = Field(default=None, description="Path to TLS CA bundle")
    tls_ciphers: Optional[List[str]] = Field(default=None, description="Allowed TLS ciphers")
    dicom_services: List[str] = Field(default_factory=lambda: ["*"], description="Supported DICOM services/SOP Classes")
    transfer_syntaxes: List[str] = Field(
        default_factory=lambda: ["*"], description="Proposed/acceptable transfer syntaxes"
    )
    dicomweb_enabled: bool = Field(default=True, description="Enable DICOMweb support")
    dicomweb_url_prefix: Optional[str] = Field(default="/dicomweb", description="DICOMweb URL prefix")
    dicomweb_qido_enabled: bool = Field(default=True, description="Enable QIDO-RS")
    dicomweb_wado_enabled: bool = Field(default=True, description="Enable WADO-RS")
    dicomweb_stow_enabled: bool = Field(default=True, description="Enable STOW-RS")
    description: Optional[str] = Field(default=None, description="Human readable description")
    test_id: Optional[str] = Field(default=None, description="Test identifier if ephemeral")

    @field_validator("test_id", mode="before")
    @classmethod
    def normalize_test_id(cls, v: Any) -> Optional[str]:
        if isinstance(v, str):
            stripped = v.strip()
            return stripped if stripped else None
        return v


class DICOMConfigurationUpdateRequest(BaseModel):
    """API request model to update an existing DICOM Configuration."""

    ae_title: Optional[str] = Field(default=None, description="Application Entity Title")
    roles: Optional[List[str]] = Field(default=None, description="Roles: SCP, SCU")
    host: Optional[str] = Field(default=None, description="Host or bind address")
    port: Optional[int] = Field(default=None, description="TCP port")
    is_local: Optional[bool] = Field(
        default=None,
        description="Whether this is a local server (True) bound to a local interface or remote SCP (False)",
    )
    tls_enabled: Optional[bool] = Field(default=None, description="Whether TLS is enabled")
    tls_cert_path: Optional[str] = Field(default=None, description="Path to TLS certificate file")
    tls_key_path: Optional[str] = Field(default=None, description="Path to TLS private key file")
    tls_ca_path: Optional[str] = Field(default=None, description="Path to TLS CA bundle")
    tls_ciphers: Optional[List[str]] = Field(default=None, description="Allowed TLS ciphers")
    dicom_services: Optional[List[str]] = Field(default=None, description="Supported DICOM services/SOP Classes")
    transfer_syntaxes: Optional[List[str]] = Field(default=None, description="Proposed/acceptable transfer syntaxes")
    dicomweb_enabled: Optional[bool] = Field(default=None, description="Enable DICOMweb support")
    dicomweb_url_prefix: Optional[str] = Field(default=None, description="DICOMweb URL prefix")
    dicomweb_qido_enabled: Optional[bool] = Field(default=None, description="Enable QIDO-RS")
    dicomweb_wado_enabled: Optional[bool] = Field(default=None, description="Enable WADO-RS")
    dicomweb_stow_enabled: Optional[bool] = Field(default=None, description="Enable STOW-RS")
    description: Optional[str] = Field(default=None, description="Human readable description")


class SystemResponse(BaseModel):
    """API response model for the root System node."""

    id: str = Field(..., description="Deterministic System ID")
    name: str = Field(..., description="Name of the system")
    owner: str = Field(..., description="Owner user or organization")
    address: str = Field(..., description="Physical address string")
    email: str = Field(..., description="Contact email address (non-auth)")
    phone: str = Field(..., description="Contact phone number (non-auth)")
    serial_number: str = Field(..., description="System serial number")
    installed_at: datetime = Field(..., description="Installation UTC DateTime")
    test_id: Optional[str] = Field(default=None, description="Test identifier if ephemeral")
    action: Optional[str] = Field(default="CREATE", description="Auditable action state")
    created_at: Optional[datetime] = Field(default=None, description="Created timestamp (UTC)")
    updated_at: Optional[datetime] = Field(default=None, description="Updated timestamp (UTC)")
    configurations_count: int = Field(default=0, description="Number of linked DICOM configurations")
    configurations: List[DICOMConfigurationResponse] = Field(
        default_factory=list,
        description="Linked DICOM configurations",
    )

    model_config = {"populate_by_name": True}


class SystemUpdateRequest(BaseModel):
    """API request model to update mutable properties of the System node."""

    name: Optional[str] = Field(default=None, description="Name of the system")
    owner: Optional[str] = Field(default=None, description="Owner user or organization")
    address: Optional[str] = Field(default=None, description="Physical address string")
    email: Optional[str] = Field(default=None, description="Contact email address (non-auth)")
    phone: Optional[str] = Field(default=None, description="Contact phone number (non-auth)")
    serial_number: Optional[str] = Field(default=None, description="System serial number")
    installed_at: Optional[datetime] = Field(default=None, description="Installation UTC DateTime")


class SystemBootstrapResponse(BaseModel):
    """API response model for system bootstrapping."""

    success: bool = Field(..., description="Whether bootstrap succeeded")
    message: str = Field(..., description="Bootstrap status message")
    system: SystemResponse = Field(..., description="System node representation")
    promiscuous_config: Optional[DICOMConfigurationResponse] = Field(
        default=None,
        description="Promiscuous main DICOM configuration",
    )


class TestCleanupResponse(BaseModel):
    """API response model for test data cleanup."""

    success: bool = Field(..., description="Whether cleanup succeeded")
    message: str = Field(..., description="Status message")
    total_deleted: int = Field(default=0, description="Total count of nodes detached and deleted")
    deleted_by_label: Dict[str, int] = Field(default_factory=dict, description="Breakdown of deleted nodes by label")
    labels_cleaned: List[str] = Field(default_factory=list, description="List of labels scanned and cleaned")
    test_id: Optional[str] = Field(default=None, description="Test identifier that was cleaned up if filtered")
