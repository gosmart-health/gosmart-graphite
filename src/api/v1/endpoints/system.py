from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from core.auth_dependency import get_current_group_context, get_current_user
from model.system import (
    DICOMConfigurationCreateRequest,
    DICOMConfigurationResponse,
    DICOMConfigurationUpdateRequest,
    SystemBootstrapResponse,
    SystemResponse,
    SystemUpdateRequest,
    TestCleanupResponse,
)
from service.system_service import system_service

router = APIRouter(
    tags=["System & Configurations"],
    dependencies=[Depends(get_current_user), Depends(get_current_group_context)],
)


# -----------------------------------------------------------------------------
# System Node Management
# -----------------------------------------------------------------------------


@router.get("/system", response_model=SystemResponse)
async def get_system_node(
    include_configs: bool = Query(default=True, description="Whether to include linked DICOM configurations"),
    test_id: Optional[str] = Query(default=None, description="Optional test session identifier"),
):
    """Retrieve the root System node properties and attached configurations."""
    sys_data = await system_service.get_system(include_configs=include_configs, test_id=test_id)
    if not sys_data:
        raise HTTPException(
            status_code=404,
            detail="System node not found. Trigger POST /api/v1/system/bootstrap to initialize.",
        )
    return sys_data


@router.put("/system", response_model=SystemResponse)
async def update_system_node(
    req: SystemUpdateRequest,
    x_user_id: Optional[str] = Header(default="installer", alias="X-User-ID"),
    test_id: Optional[str] = Query(default=None, description="Optional test session identifier"),
):
    """Update mutable properties of the root System node (performed by system installer)."""
    updated = await system_service.update_system(data=req, acted_by=x_user_id or "installer", test_id=test_id)
    if not updated:
        raise HTTPException(status_code=404, detail="System node not found to update")
    return updated


@router.post("/system/bootstrap", response_model=SystemBootstrapResponse)
async def bootstrap_system(
    x_user_id: Optional[str] = Header(default="system", alias="X-User-ID"),
    test_id: Optional[str] = Query(default=None, description="Optional test session identifier"),
):
    """Idempotently bootstrap the root System node and default promiscuous DICOM configuration."""
    try:
        result = await system_service.bootstrap_system(acted_by=x_user_id or "system", test_id=test_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to bootstrap system: {str(e)}")


# -----------------------------------------------------------------------------
# Test Data Management
# -----------------------------------------------------------------------------


@router.post("/system/test/cleanup", response_model=TestCleanupResponse)
async def cleanup_test_data_endpoint(
    test_id: Optional[str] = Query(
        default=None,
        description="Optional specific test_id to clean up; if omitted, cleans up all test data across target labels",
    ),
    labels: Optional[List[str]] = Query(
        default=None,
        description=(
            "Optional subset of node labels to clean. "
            "Defaults to System, DICOMConfiguration, Instance, Patient, Series, Study"
        ),
    ),
    x_user_id: Optional[str] = Header(default="installer", alias="X-User-ID"),
) -> TestCleanupResponse:
    """Full test cleanup endpoint to detach and delete test data across property graph labels."""
    try:
        res = await system_service.cleanup_all_test_data(test_id=test_id, labels=labels)
        return TestCleanupResponse(**res)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cleanup test data: {str(e)}")


# -----------------------------------------------------------------------------
# DICOM Configuration Management (CRUD)
# -----------------------------------------------------------------------------


@router.get("/configurations/dicom", response_model=List[DICOMConfigurationResponse])
async def list_dicom_configurations(
    role: Optional[str] = Query(default=None, description="Filter by DICOM role (SCP or SCU)"),
    is_promiscuous: Optional[bool] = Query(default=None, description="Filter by promiscuous mode flag"),
    is_local: Optional[bool] = Query(
        default=None, description="Filter by local server flag (True for local server, False for remote SCP)"
    ),
    include_tests: bool = Query(default=False, description="Whether to include test configurations"),
    test_id: Optional[str] = Query(default=None, description="Filter by test identifier"),
):
    """List active DICOM configurations linked to the System."""
    try:
        configs = await system_service.list_configurations(
            role=role,
            is_promiscuous=is_promiscuous,
            is_local=is_local,
            include_tests=include_tests,
            test_id=test_id,
        )
        return configs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list DICOM configurations: {str(e)}")


@router.post("/configurations/dicom", response_model=DICOMConfigurationResponse, status_code=201)
async def create_dicom_configuration(
    req: DICOMConfigurationCreateRequest,
    x_user_id: Optional[str] = Header(default="installer", alias="X-User-ID"),
):
    """Create a new DICOM Configuration and link it to the root System node."""
    try:
        created = await system_service.create_configuration(config_data=req, acted_by=x_user_id or "installer")
        return created
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create DICOM configuration: {str(e)}")


@router.get("/configurations/dicom/{config_id}", response_model=DICOMConfigurationResponse)
async def get_dicom_configuration(config_id: str):
    """Retrieve single DICOM Configuration by its deterministic ID."""
    cfg = await system_service.get_configuration(config_id=config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"DICOM configuration '{config_id}' not found")
    return cfg


@router.put("/configurations/dicom/{config_id}", response_model=DICOMConfigurationResponse)
async def update_dicom_configuration(
    config_id: str,
    req: DICOMConfigurationUpdateRequest,
    x_user_id: Optional[str] = Header(default="installer", alias="X-User-ID"),
):
    """Update mutable properties of an existing DICOM Configuration."""
    updated = await system_service.update_configuration(
        config_id=config_id, update_data=req, acted_by=x_user_id or "installer"
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"DICOM configuration '{config_id}' not found")
    return updated


@router.delete("/configurations/dicom/{config_id}")
async def delete_dicom_configuration(
    config_id: str,
    hard: bool = Query(default=False, description="Perform hard delete instead of soft delete"),
    x_user_id: Optional[str] = Header(default="installer", alias="X-User-ID"),
):
    """Soft-delete (or hard-delete if specified) a DICOM Configuration."""
    deleted = await system_service.delete_configuration(
        config_id=config_id, acted_by=x_user_id or "installer", hard_delete=hard
    )
    if not deleted:
        raise HTTPException(status_code=404, detail=f"DICOM configuration '{config_id}' not found or already deleted")
    return {
        "success": True,
        "message": f"DICOM configuration '{config_id}' deleted successfully",
        "hard_deleted": hard,
    }


@router.post("/configurations/dicom/{config_id}/restore", response_model=DICOMConfigurationResponse)
async def restore_dicom_configuration(
    config_id: str,
    x_user_id: Optional[str] = Header(default="installer", alias="X-User-ID"),
):
    """Restore a previously soft-deleted DICOM Configuration."""
    restored = await system_service.restore_configuration(config_id=config_id, acted_by=x_user_id or "installer")
    if not restored:
        raise HTTPException(
            status_code=404,
            detail=f"Soft-deleted DICOM configuration '{config_id}' not found to restore",
        )
    return restored
