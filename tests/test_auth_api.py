import uuid

import pytest
from fastapi.testclient import TestClient

from core.security import decode_token
from db.neo4j import neo4j_client
from main import app
from service.graph_service import graph_service
from service.group_service import group_service


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.anyio
async def test_auth_register_login_and_refresh_flow(client):
    test_id = f"test_api_auth_{uuid.uuid4().hex[:8]}"

    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        # Create a test group
        facility = await group_service.create_group(
            name="San Mateo Imaging Center",
            code=f"SMC_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )

        email = f"dr.chen_{uuid.uuid4().hex[:4]}@sanmateo.org"
        username = f"dr_chen_{uuid.uuid4().hex[:4]}"
        password = "SecurePassword2026!"

        # 1. Register User
        reg_payload = {
            "username": username,
            "email": email,
            "password": password,
            "display_name": "Dr. Chen",
            "system_roles": ["USER", "PHYSICIAN"],
            "group_id": facility["id"],
            "group_roles": ["Radiologist"],
            "test_id": test_id,
        }
        reg_res = client.post("/api/v1/auth/register", json=reg_payload)
        assert reg_res.status_code == 201
        reg_data = reg_res.json()
        assert reg_data["primary_email"] == email.lower()
        assert reg_data["display_name"] == "Dr. Chen"
        assert reg_data["is_active"] is True
        user_id = reg_data["id"]

        # Duplicate email registration must return 409 Conflict
        dup_res = client.post("/api/v1/auth/register", json=reg_payload)
        assert dup_res.status_code == 409

        # 2. Login with valid credentials
        login_res = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password, "test_id": test_id},
        )
        assert login_res.status_code == 200
        token_data = login_res.json()
        assert "access_token" in token_data
        assert "refresh_token" in token_data
        assert token_data["token_type"] == "bearer"
        assert token_data["user"]["id"] == user_id
        assert len(token_data["user"]["affiliations"]) == 1
        assert token_data["user"]["affiliations"][0]["group_id"] == facility["id"]

        # Validate access token claims
        decoded_access = decode_token(token_data["access_token"])
        assert decoded_access["sub"] == user_id
        assert decoded_access["email"] == email.lower()
        assert decoded_access["token_type"] == "access"

        # 3. Login with invalid password must return 401 Unauthorized
        bad_login_res = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "WrongPassword!"},
        )
        assert bad_login_res.status_code == 401

        # 4. Refresh token rotation
        refresh_res = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": token_data["refresh_token"]},
        )
        assert refresh_res.status_code == 200
        refresh_data = refresh_res.json()
        assert "access_token" in refresh_data
        assert refresh_data["token_type"] == "bearer"

        # 5. Invalid refresh token returns 401
        bad_refresh_res = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.jwt.token"},
        )
        assert bad_refresh_res.status_code == 401

        # 6. Create API Key for this user
        api_key_res = client.post(
            "/api/v1/auth/api-keys",
            json={
                "user_id": user_id,
                "name": "CLI Operator Key",
                "scopes": ["mcp:read", "dicom:query"],
                "test_id": test_id,
            },
        )
        assert api_key_res.status_code == 201
        key_data = api_key_res.json()
        assert key_data["raw_api_key"].startswith("gs_live_")
        assert key_data["scopes"] == ["mcp:read", "dicom:query"]

    finally:
        await neo4j_client.close()


@pytest.mark.anyio
async def test_auth_google_oauth_jit_provisioning(client):
    test_id = f"test_api_google_{uuid.uuid4().hex[:8]}"

    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        facility = await group_service.create_group(
            name="Pacific Medical Center",
            code=f"PMC_{uuid.uuid4().hex[:4]}",
            test_id=test_id,
        )

        google_sub = f"sub_{uuid.uuid4().hex[:8]}"
        email = f"google_user_{uuid.uuid4().hex[:4]}@gmail.com"
        mock_id_token = f"mock_google_{google_sub}:{email}:Dr. Google Physician"

        # Authenticate with Google ID token
        oauth_res = client.post(
            "/api/v1/auth/oauth/google",
            json={
                "id_token": mock_id_token,
                "default_group_id": facility["id"],
                "default_group_roles": ["Viewer"],
                "test_id": test_id,
            },
        )
        assert oauth_res.status_code == 200
        token_data = oauth_res.json()
        assert "access_token" in token_data
        assert token_data["user"]["primary_email"] == email.lower()
        assert token_data["user"]["display_name"] == "Dr. Google Physician"
        assert token_data["user"]["is_shadow"] is True
        assert len(token_data["user"]["affiliations"]) == 1

        # Re-login with same Google token returns same user
        relogin_res = client.post(
            "/api/v1/auth/oauth/google",
            json={"id_token": mock_id_token, "test_id": test_id},
        )
        assert relogin_res.status_code == 200
        assert relogin_res.json()["user"]["id"] == token_data["user"]["id"]

    finally:
        await neo4j_client.close()
