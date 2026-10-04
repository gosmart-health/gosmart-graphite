from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.config import get_settings
from core.logging import get_logger
from core.security import create_access_token, create_refresh_token, decode_token
from service.group_service import group_service
from service.user_service import user_service

logger = get_logger("gosmart_gs.auth_api")

router = APIRouter(prefix="/auth", tags=["Authentication & Identity"])


class LoginRequest(BaseModel):
    username: str = Field(..., description="Username or primary email address")
    password: str = Field(..., description="Plaintext password")
    test_id: Optional[str] = Field(default=None, description="Optional test session identifier")


class GoogleOAuthRequest(BaseModel):
    id_token: str = Field(..., description="Google OAuth2 / OIDC ID Token")
    default_group_id: Optional[str] = Field(
        default=None, description="Initial group to affiliate with on JIT provisioning"
    )
    default_group_roles: Optional[List[str]] = Field(default=None, description="Initial roles within the default group")
    test_id: Optional[str] = Field(default=None, description="Optional test session identifier")


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., description="Signed JWT Refresh Token")


class RegisterRequest(BaseModel):
    username: str = Field(..., description="Unique alphanumeric username")
    email: str = Field(..., description="Primary contact email")
    password: str = Field(..., min_length=8, description="Password (min 8 chars)")
    display_name: Optional[str] = Field(default=None, description="Full display name")
    system_roles: Optional[List[str]] = Field(default=None, description="Global platform roles")
    group_id: Optional[str] = Field(default=None, description="Initial facility/group affiliation")
    group_roles: Optional[List[str]] = Field(default=None, description="Initial roles within group")
    test_id: Optional[str] = Field(default=None, description="Optional test session identifier")


class CreateApiKeyRequest(BaseModel):
    user_id: str = Field(..., description="Target User ID")
    name: str = Field(..., description="Human-readable label for this API key")
    scopes: Optional[List[str]] = Field(default=None, description="Permission scopes")
    expires_at: Optional[datetime] = Field(default=None, description="Expiration date in UTC")
    test_id: Optional[str] = Field(default=None, description="Optional test session identifier")


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: Dict[str, Any]


class RefreshTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


async def _verify_google_token(id_token: str) -> Dict[str, Any]:
    """Verify a Google ID token and return claims (supports mock tokens in testing)."""
    if id_token.startswith("mock_google_"):
        # Simulated test token format: mock_google_<sub_id>:<email>:<name>
        parts = id_token.split(":")
        sub_part = parts[0].replace("mock_google_", "")
        email_part = parts[1] if len(parts) > 1 else f"{sub_part}@mock.google.com"
        name_part = parts[2] if len(parts) > 2 else "Mock Google User"
        return {
            "sub": sub_part,
            "email": email_part,
            "name": name_part,
            "email_verified": "true",
            "picture": "https://lh3.googleusercontent.com/photo.jpg",
        }

    settings = get_settings()
    url = f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        if response.status_code != 200:
            logger.warning("google_token_verification_failed", status_code=response.status_code)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google OAuth ID token",
            )
        claims = response.json()

    # Optional audience check if client ID is configured
    if settings.GOOGLE_OAUTH_CLIENT_ID:
        aud = claims.get("aud")
        if aud != settings.GOOGLE_OAUTH_CLIENT_ID:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google OAuth ID token audience mismatch",
            )

    return claims


async def _build_token_payload(user: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch user's affiliations and compile JWT claims payload."""
    user_id = user["id"]
    affiliations = await group_service.get_user_affiliations(user_id)
    return {
        "sub": user_id,
        "email": user.get("primary_email", ""),
        "display_name": user.get("display_name", ""),
        "system_roles": user.get("system_roles", ["USER"]),
        "is_shadow": user.get("is_shadow", False),
        "affiliations": affiliations,
    }


@router.post("/register", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def register_user(req: RegisterRequest):
    """Register a new local user account."""
    existing_user = await user_service.get_user_by_email(req.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{req.email}' already exists",
        )

    try:
        user = await user_service.create_local_user(
            username=req.username,
            email=req.email,
            password=req.password,
            display_name=req.display_name,
            system_roles=req.system_roles,
            group_id=req.group_id,
            group_roles=req.group_roles,
            test_id=req.test_id,
            acted_by=f"user:{req.username}",
        )
        return user
    except Exception as e:
        logger.error("user_registration_failed", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {str(e)}",
        )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """Authenticate with username/email and password to receive JWT tokens."""
    user = await user_service.authenticate_local(identifier=req.username, password=req.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    settings = get_settings()
    payload = await _build_token_payload(user)

    access_token = create_access_token(payload)
    refresh_token = create_refresh_token(payload)
    expires_in = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

    user_details = {**user, "affiliations": payload["affiliations"]}
    logger.info("user_logged_in", user_id=user["id"], email=user.get("primary_email"))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=expires_in,
        user=user_details,
    )


@router.post("/oauth/google", response_model=TokenResponse)
async def oauth_google(req: GoogleOAuthRequest):
    """Authenticate via Google OAuth ID token, with automatic JIT Shadow User provisioning."""
    claims = await _verify_google_token(req.id_token)
    google_sub = claims.get("sub")
    email = claims.get("email")
    display_name = claims.get("name")
    picture = claims.get("picture")

    if not google_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google ID token missing required claims ('sub', 'email')",
        )

    user = await user_service.find_or_create_shadow_user(
        provider="google",
        external_sub=google_sub,
        email=email,
        display_name=display_name,
        default_group_id=req.default_group_id,
        default_group_roles=req.default_group_roles,
        profile_metadata={"avatar_url": picture, "google_claims": claims},
        test_id=req.test_id,
        acted_by=f"oauth:google:{email}",
    )

    settings = get_settings()
    payload = await _build_token_payload(user)

    access_token = create_access_token(payload)
    refresh_token = create_refresh_token(payload)
    expires_in = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

    user_details = {**user, "affiliations": payload["affiliations"]}
    logger.info("google_oauth_success", user_id=user["id"], email=email, is_shadow=user.get("is_shadow"))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=expires_in,
        user=user_details,
    )


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_access_token(req: RefreshTokenRequest):
    """Rotate an expired access token using a valid JWT Refresh Token."""
    try:
        decoded = decode_token(req.refresh_token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired refresh token: {str(e)}",
        )

    if decoded.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provided token is not a refresh token",
        )

    user_id = decoded.get("sub")
    user = await user_service.get_user_by_id(user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive or not found",
        )

    settings = get_settings()
    payload = await _build_token_payload(user)
    new_access_token = create_access_token(payload)
    expires_in = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60

    return RefreshTokenResponse(
        access_token=new_access_token,
        token_type="bearer",
        expires_in=expires_in,
    )


@router.post("/api-keys", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_user_api_key(req: CreateApiKeyRequest):
    """Generate and attach a secure API key for programmatic / MCP agent access."""
    user = await user_service.get_user_by_id(req.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{req.user_id}' not found",
        )

    result = await user_service.create_api_key(
        user_id=req.user_id,
        name=req.name,
        scopes=req.scopes,
        expires_at=req.expires_at,
        test_id=req.test_id,
        acted_by=f"user:{user.get('primary_email')}",
    )
    logger.info("api_key_created", user_id=req.user_id, name=req.name)
    return result
