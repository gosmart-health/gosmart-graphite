import uuid

import pytest

from db.neo4j import neo4j_client
from service.graph_service import graph_service
from service.group_service import group_service
from service.user_service import user_service


@pytest.mark.anyio
async def test_local_user_lifecycle_and_authentication():
    test_id = f"test_usr_{uuid.uuid4().hex[:8]}"

    # Pre-test cleanup
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        # Create a test facility group
        facility = await group_service.create_group(
            name="Coast Radiology Center",
            code=f"CRC_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )

        email = f"dr.smith_{uuid.uuid4().hex[:4]}@hospital.org"
        username = f"dr_smith_{uuid.uuid4().hex[:4]}"
        password = "SecurePassword2026!"

        # 1. Create Local User
        user = await user_service.create_local_user(
            username=username,
            email=email,
            password=password,
            display_name="Dr. Jane Smith",
            system_roles=["USER", "PHYSICIAN"],
            group_id=facility["id"],
            group_roles=["Radiologist", "Admin"],
            test_id=test_id,
        )
        assert user["primary_email"] == email.lower()
        assert user["display_name"] == "Dr. Jane Smith"
        assert user["is_active"] is True
        assert user["is_shadow"] is False

        # 2. Authenticate via username
        auth_by_user = await user_service.authenticate_local(identifier=username, password=password)
        assert auth_by_user is not None
        assert auth_by_user["id"] == user["id"]

        # 3. Authenticate via email
        auth_by_email = await user_service.authenticate_local(identifier=email, password=password)
        assert auth_by_email is not None
        assert auth_by_email["id"] == user["id"]

        # 4. Failed authentication with wrong password
        failed_auth = await user_service.authenticate_local(identifier=username, password="WrongPassword!")
        assert failed_auth is None

        # 5. Check user affiliations
        affiliations = await group_service.get_user_affiliations(user["id"])
        assert len(affiliations) == 1
        assert affiliations[0]["group_id"] == facility["id"]
        assert "Radiologist" in affiliations[0]["roles"]
        assert affiliations[0]["status"] == "ACTIVE"

    finally:
        await neo4j_client.close()


@pytest.mark.anyio
async def test_shadow_user_jit_provisioning_and_promotion():
    test_id = f"test_shadow_{uuid.uuid4().hex[:8]}"

    # Pre-test cleanup
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        facility = await group_service.create_group(
            name="General Hospital Clinic",
            code=f"GHC_{uuid.uuid4().hex[:4]}",
            test_id=test_id,
        )

        google_sub = f"google_sub_{uuid.uuid4().hex[:10]}"
        email = f"oauth_user_{uuid.uuid4().hex[:4]}@gmail.com"

        # 1. JIT Provision Shadow User on first Google OAuth login
        shadow_user = await user_service.find_or_create_shadow_user(
            provider="google",
            external_sub=google_sub,
            email=email,
            display_name="Google Doctor",
            default_group_id=facility["id"],
            default_group_roles=["Viewer"],
            profile_metadata={"avatar_url": "https://lh3.googleusercontent.com/photo.jpg"},
            test_id=test_id,
        )
        assert shadow_user["primary_email"] == email.lower()
        assert shadow_user["is_shadow"] is True
        assert shadow_user["is_active"] is True

        # 2. Re-login with same Google credentials returns existing user
        relogin_user = await user_service.find_or_create_shadow_user(
            provider="google",
            external_sub=google_sub,
            email=email,
            test_id=test_id,
        )
        assert relogin_user["id"] == shadow_user["id"]

        # 3. Promote Account by attaching local password
        promoted_username = f"google_doc_{uuid.uuid4().hex[:6]}"
        promoted = await user_service.attach_password_to_user(
            user_id=shadow_user["id"],
            password="PromotedPassword123!",
            username=promoted_username,
            test_id=test_id,
        )
        assert promoted["is_shadow"] is False

        # 4. Verify user can now authenticate locally with newly set password
        local_auth = await user_service.authenticate_local(
            identifier=promoted_username,
            password="PromotedPassword123!",
        )
        assert local_auth is not None
        assert local_auth["id"] == shadow_user["id"]

    finally:
        await neo4j_client.close()


@pytest.mark.anyio
async def test_api_key_creation_and_authentication():
    test_id = f"test_apikey_{uuid.uuid4().hex[:8]}"

    # Pre-test cleanup
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        # Create a user to attach API key to
        user = await user_service.create_local_user(
            username=f"agent_user_{uuid.uuid4().hex[:4]}",
            email=f"agent_{uuid.uuid4().hex[:4]}@gosmart.health",
            password="AgentPassword123!",
            test_id=test_id,
        )

        # 1. Create API key for MCP Agent
        key_res = await user_service.create_api_key(
            user_id=user["id"],
            name="MCP Clinical Agent Token",
            scopes=["mcp:full", "dicom:read"],
            test_id=test_id,
        )
        raw_key = key_res["raw_api_key"]
        assert raw_key.startswith("gs_live_")
        assert key_res["name"] == "MCP Clinical Agent Token"
        assert key_res["scopes"] == ["mcp:full", "dicom:read"]

        # 2. Authenticate using the generated API Key
        auth_user = await user_service.authenticate_api_key(raw_key)
        assert auth_user is not None
        assert auth_user["id"] == user["id"]
        assert auth_user["api_key_scopes"] == ["mcp:full", "dicom:read"]
        assert auth_user["api_key_name"] == "MCP Clinical Agent Token"

        # 3. Authenticate with invalid API key
        invalid_auth = await user_service.authenticate_api_key("gs_live_invalid_key_99999999")
        assert invalid_auth is None

    finally:
        await neo4j_client.close()
