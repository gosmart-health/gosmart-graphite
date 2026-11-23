from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from gosmart_graphite.model.dicom_config import DICOMConfigurationResponse


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
