from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.logging import bind_log_context, get_logger
from core.security import decode_token
from service.group_service import group_service
from service.user_service import user_service

logger = get_logger("gosmart_gs.auth_dep")

# Optional Bearer schema for swagger UI & header extraction
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Dict[str, Any]:
    """Extract and validate the authenticated User from JWT Bearer Token or API Key.

    Raises HTTP 401 Unauthorized if missing, expired, or invalid.
    """
    raw_token: Optional[str] = None
    if credentials and credentials.credentials:
        raw_token = credentials.credentials.strip()

    # 1. API Key Authentication (via X-API-Key header or Bearer gs_live_...)
    api_key = x_api_key or (raw_token if raw_token and raw_token.startswith("gs_live_") else None)
    if api_key:
        user = await user_service.authenticate_api_key(api_key)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API Key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.current_user = user
        bind_log_context(user_id=user["id"], auth_type="api_key")
        return user

    # 2. JWT Access Token Authentication
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(raw_token)
    except Exception as e:
        logger.warning("jwt_validation_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not validate credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provided token is not an access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: missing 'sub'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await user_service.get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive or no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user["token_claims"] = payload
    request.state.current_user = user
    bind_log_context(user_id=user["id"], user_email=user.get("primary_email"), auth_type="jwt")
    return user


async def get_optional_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Optional[Dict[str, Any]]:
    """Optionally extract the authenticated User if credentials are provided."""
    try:
        return await get_current_user(request=request, credentials=credentials, x_api_key=x_api_key)
    except HTTPException:
        return None


async def get_current_group_context(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
    x_group_id: Optional[str] = Header(default=None, alias="X-Group-ID"),
) -> Optional[Dict[str, Any]]:
    """Validate and resolve the active working Group context for the current user.

    If X-Group-ID is provided, verifies that the user has an active membership or
    descendant access to that facility.
    """
    if not x_group_id:
        # Resolve user's primary affiliation if available
        affiliations = await group_service.get_user_affiliations(current_user["id"])
        primary = next((a for a in affiliations if a.get("is_primary")), None)
        return primary

    target_group_id = x_group_id.strip()
    accessible_groups = await group_service.get_effective_accessible_groups(current_user["id"])
    matched = next((g for g in accessible_groups if g["id"] == target_group_id), None)

    if not matched:
        # Check if user has global platform admin role
        system_roles = current_user.get("system_roles", [])
        if "PLATFORM_ADMIN" in system_roles or "ADMIN" in system_roles:
            group = await group_service.get_group_by_id(target_group_id)
            if group:
                return group

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User does not have active access to facility/group '{target_group_id}'",
        )

    request.state.current_group = matched
    bind_log_context(group_id=matched["id"], group_code=matched.get("code"))
    return matched


def require_roles(allowed_roles: List[str]) -> Callable:
    """Dependency factory checking that current_user has at least one of the allowed roles.

    Evaluates both global system_roles and group-specific roles.
    """

    async def _role_checker(
        current_user: Dict[str, Any] = Depends(get_current_user),
        group_context: Optional[Dict[str, Any]] = Depends(get_current_group_context),
    ) -> Dict[str, Any]:
        user_roles = set(current_user.get("system_roles", []))

        # Check global platform admin bypass
        if "PLATFORM_ADMIN" in user_roles or "ADMIN" in user_roles:
            return current_user

        # Add group-specific roles if in active group context
        if group_context and "roles" in group_context:
            user_roles.update(group_context.get("roles", []))

        # Case-insensitive comparison
        allowed_set = {r.lower() for r in allowed_roles}
        user_set = {r.lower() for r in user_roles}

        if not allowed_set.intersection(user_set):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires one of the following roles: {allowed_roles}",
            )
        return current_user

    return _role_checker
