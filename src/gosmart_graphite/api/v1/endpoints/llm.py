"""REST API endpoints for managing dynamic, group-scoped LLM configurations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from gosmart_graphite.core.auth_dependency import (
    get_current_group_context,
    get_current_user,
    require_roles,
)
from gosmart_graphite.model.llm_config import (
    LLMConfigAssignRequest,
    LLMConfigCreateRequest,
    LLMConfigResponse,
    LLMConfigUpdateRequest,
)
from gosmart_graphite.service.llm_config_service import llm_config_service

router = APIRouter(
    tags=["LLM Configurations"],
    dependencies=[Depends(get_current_user), Depends(get_current_group_context)],
)


def _resolve_actor(x_user_id: Optional[str], current_user: Dict[str, Any]) -> str:
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()
    return str(current_user.get("primary_email") or current_user.get("id") or "system")


@router.get("/configurations/llm", response_model=List[LLMConfigResponse])
async def list_llm_configurations(
    tag: Optional[str] = Query(default=None, description="Filter by LLM_Config tag (e.g. 'embedding-v1', 'router')"),
    provider: Optional[str] = Query(default=None, description="Filter by provider (e.g. 'ollama', 'vertexai')"),
    group_id: Optional[str] = Query(default=None, description="Filter to configs assigned to this Group ID"),
    include_tests: bool = Query(default=False, description="Whether to include test configurations"),
    test_id: Optional[str] = Query(default=None, description="Filter by test identifier"),
) -> List[Dict[str, Any]]:
    """List active LLM_Config nodes (with api_key masked)."""
    try:
        return await llm_config_service.list_llm_configs(
            tag=tag,
            provider=provider,
            group_id=group_id,
            include_tests=include_tests,
            test_id=test_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list LLM configurations: {exc}")


@router.post("/configurations/llm", response_model=LLMConfigResponse, status_code=201)
async def create_or_overwrite_llm_configuration(
    req: LLMConfigCreateRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["PLATFORM_ADMIN", "Admin"])),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
) -> Dict[str, Any]:
    """Create or overwrite a purpose-tagged LLM_Config and assign it to a Group."""
    acted_by = _resolve_actor(x_user_id, current_user)
    try:
        return await llm_config_service.upsert_llm_config(
            config_data=req,
            assign_group=req.assign_group,
            acted_by=acted_by,
            test_id=req.test_id,
        )
    except (ValueError, RuntimeError) as exc:
        msg = str(exc)
        status_code = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status_code, detail=msg)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to upsert LLM configuration: {exc}")


@router.get("/configurations/llm/{config_id}", response_model=LLMConfigResponse)
async def get_llm_configuration(config_id: str) -> Dict[str, Any]:
    """Retrieve a single active LLM_Config by its deterministic ID (api_key masked)."""
    cfg = await llm_config_service.get_llm_config(config_id=config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"LLM configuration '{config_id}' not found")
    return cfg


@router.put("/configurations/llm/{config_id}", response_model=LLMConfigResponse)
async def update_llm_configuration(
    config_id: str,
    req: LLMConfigUpdateRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["PLATFORM_ADMIN", "Admin"])),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
) -> Dict[str, Any]:
    """Update mutable properties of an active LLM_Config (archiving prior version via NOT_HAS_LLM_CONFIG)."""
    acted_by = _resolve_actor(x_user_id, current_user)
    try:
        updated = await llm_config_service.update_llm_config(
            config_id=config_id,
            update_data=req,
            acted_by=acted_by,
        )
    except (ValueError, RuntimeError) as exc:
        msg = str(exc)
        status_code = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status_code, detail=msg)
    if not updated:
        raise HTTPException(status_code=404, detail=f"LLM configuration '{config_id}' not found")
    return updated


@router.post("/configurations/llm/{config_id}/assign", response_model=LLMConfigResponse)
async def assign_llm_configuration_to_group(
    config_id: str,
    req: LLMConfigAssignRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["PLATFORM_ADMIN", "Admin"])),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
) -> Dict[str, Any]:
    """Assign an active LLM_Config to a target sponsoring Group via HAS_LLM_CONFIG."""
    acted_by = _resolve_actor(x_user_id, current_user)
    try:
        assigned = await llm_config_service.assign_to_group(
            config_id=config_id,
            group_identifier=req.group,
            acted_by=acted_by,
            test_id=req.test_id,
        )
    except (ValueError, RuntimeError) as exc:
        msg = str(exc)
        status_code = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status_code, detail=msg)
    if not assigned:
        raise HTTPException(status_code=404, detail=f"LLM configuration '{config_id}' not found")
    return assigned


@router.delete("/configurations/llm/{config_id}/groups/{group_id}")
async def dissociate_llm_configuration_from_group(
    config_id: str,
    group_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["PLATFORM_ADMIN", "Admin"])),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
) -> Dict[str, Any]:
    """Soft-dissociate a Group from an LLM_Config (swapping HAS_LLM_CONFIG to NOT_HAS_LLM_CONFIG)."""
    acted_by = _resolve_actor(x_user_id, current_user)
    removed = await llm_config_service.dissociate_from_group(
        config_id=config_id,
        group_id=group_id,
        acted_by=acted_by,
    )
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Active HAS_LLM_CONFIG link between Group '{group_id}' and LLM_Config '{config_id}' not found",
        )
    return {
        "success": True,
        "config_id": config_id,
        "group_id": group_id,
        "message": f"LLM configuration '{config_id}' dissociated from group '{group_id}'",
    }


@router.delete("/configurations/llm/{config_id}")
async def delete_llm_configuration(
    config_id: str,
    hard: bool = Query(default=False, description="Perform hard delete instead of soft delete"),
    current_user: Dict[str, Any] = Depends(require_roles(["PLATFORM_ADMIN", "Admin"])),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
) -> Dict[str, Any]:
    """Soft-delete (or hard-delete if specified) an LLM_Config node."""
    acted_by = _resolve_actor(x_user_id, current_user)
    deleted = await llm_config_service.delete_llm_config(
        config_id=config_id,
        acted_by=acted_by,
        hard_delete=hard,
    )
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"LLM configuration '{config_id}' not found or already deleted",
        )
    return {
        "success": True,
        "message": f"LLM configuration '{config_id}' deleted successfully",
        "hard_deleted": hard,
    }


@router.post("/configurations/llm/{config_id}/restore", response_model=LLMConfigResponse)
async def restore_llm_configuration(
    config_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["PLATFORM_ADMIN", "Admin"])),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
) -> Dict[str, Any]:
    """Restore a previously soft-deleted LLM_Config node and its negated group links."""
    acted_by = _resolve_actor(x_user_id, current_user)
    try:
        restored = await llm_config_service.restore_llm_config(config_id=config_id, acted_by=acted_by)
    except (ValueError, RuntimeError) as exc:
        msg = str(exc)
        status_code = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=status_code, detail=msg)
    if not restored:
        raise HTTPException(
            status_code=404,
            detail=f"Soft-deleted LLM configuration '{config_id}' not found to restore",
        )
    return restored
