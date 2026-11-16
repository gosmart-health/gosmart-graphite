from datetime import timedelta

import jwt
import pytest

from gosmart_graphite.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_api_key,
    verify_password,
)


def test_password_hashing_and_verification():
    raw_password = "SecretPassword123!@#"
    hashed = hash_password(raw_password)

    assert hashed.startswith("$argon2")
    assert verify_password(raw_password, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False
    assert verify_password("", hashed) is False


def test_api_key_generation_and_verification():
    raw_key, prefix, key_hash = generate_api_key(prefix="gs_test_")

    assert raw_key.startswith("gs_test_")
    assert prefix == "gs_test_"
    assert len(key_hash) == 64  # SHA-256 hex string length
    assert hash_api_key(raw_key) == key_hash

    assert verify_api_key(raw_key, key_hash) is True
    assert verify_api_key("gs_test_invalid_key_12345", key_hash) is False


def test_jwt_access_and_refresh_token_lifecycle():
    payload = {
        "sub": "user_001",
        "email": "doctor@hospital.org",
        "roles": ["Radiologist", "Admin"],
    }

    access_token = create_access_token(payload, expires_delta=timedelta(minutes=15))
    assert isinstance(access_token, str)

    decoded = decode_token(access_token)
    assert decoded["sub"] == "user_001"
    assert decoded["email"] == "doctor@hospital.org"
    assert decoded["roles"] == ["Radiologist", "Admin"]
    assert decoded["token_type"] == "access"
    assert "exp" in decoded
    assert "iat" in decoded

    refresh_token = create_refresh_token(payload, expires_delta=timedelta(days=7))
    decoded_refresh = decode_token(refresh_token)
    assert decoded_refresh["sub"] == "user_001"
    assert decoded_refresh["token_type"] == "refresh"


def test_jwt_token_expiration():
    payload = {"sub": "expired_user"}
    # Token expiring in 1 millisecond
    short_token = create_access_token(payload, expires_delta=timedelta(seconds=-1))

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(short_token)

    # If verify_exp is False, it should decode successfully
    decoded_unverified = decode_token(short_token, verify_exp=False)
    assert decoded_unverified["sub"] == "expired_user"


def test_jwt_token_tampering():
    payload = {"sub": "legit_user"}
    token = create_access_token(payload)

    parts = token.split(".")
    tampered_token = f"{parts[0]}.{parts[1]}abc.{parts[2]}"

    with pytest.raises(jwt.PyJWTError):
        decode_token(tampered_token)
