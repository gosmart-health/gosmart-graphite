import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from core.config import get_settings

# Standard Argon2id password hasher (OWASP recommended defaults)
_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return _password_hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    try:
        return _password_hasher.verify(hashed_password, plain_password)
    except VerifyMismatchError, Exception:
        return False


def hash_api_key(raw_api_key: str) -> str:
    """Compute a deterministic SHA-256 hex digest of a raw API key."""
    return hashlib.sha256(raw_api_key.encode("utf-8")).hexdigest()


def generate_api_key(prefix: Optional[str] = None) -> Tuple[str, str, str]:
    """Generate a secure, random API key with prefix, returning (raw_key, prefix, sha256_hash).

    Returns:
        Tuple containing:
        - raw_api_key: The full plaintext key (e.g. 'gs_live_abc123...') to show to user once
        - prefix: The key prefix used for indexing/lookup (e.g. 'gs_live_')
        - sha256_hash: The SHA-256 hash to store securely in the database
    """
    settings = get_settings()
    key_prefix = prefix or settings.API_KEY_PREFIX
    random_bytes = secrets.token_urlsafe(32)
    raw_key = f"{key_prefix}{random_bytes}"
    key_hash = hash_api_key(raw_key)
    return raw_key, key_prefix, key_hash


def verify_api_key(raw_api_key: str, stored_hash: str) -> bool:
    """Verify a raw API key against a stored SHA-256 hash using constant-time comparison."""
    calculated_hash = hash_api_key(raw_api_key)
    return hmac.compare_digest(calculated_hash, stored_hash)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Generate a signed JWT Access Token containing user claims and expiration."""
    settings = get_settings()
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update(
        {
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
            "token_type": "access",
        }
    )
    secret = settings.JWT_SECRET_KEY.get_secret_value()
    return jwt.encode(to_encode, secret, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Generate a signed JWT Refresh Token with extended validity."""
    settings = get_settings()
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode.update(
        {
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
            "token_type": "refresh",
        }
    )
    secret = settings.JWT_SECRET_KEY.get_secret_value()
    return jwt.encode(to_encode, secret, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, verify_exp: bool = True) -> Dict[str, Any]:
    """Decode and validate a signed JWT token.

    Raises jwt.PyJWTError (ExpiredSignatureError, InvalidTokenError, etc.) on invalid tokens.
    """
    settings = get_settings()
    secret = settings.JWT_SECRET_KEY.get_secret_value()
    options = {"verify_exp": verify_exp}
    return jwt.decode(
        token,
        secret,
        algorithms=[settings.JWT_ALGORITHM],
        options=options,
    )
