import uuid
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from gosmart_graphite.cli.admin import cmd_create_admin, cmd_list_admins, cmd_reset_password
from gosmart_graphite.core.config import Settings
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.group_service import group_service
from gosmart_graphite.service.user_service import user_service


@pytest.mark.anyio
async def test_bootstrap_admin_user_creation():
    """Verify initial bootstrap creates PLATFORM_ADMIN and affiliates with Catch-All group."""
    test_id = f"test_boot_{uuid.uuid4().hex[:6]}"
    admin_email = f"bootstrap_admin_{uuid.uuid4().hex[:6]}@gosmart.health"
    admin_pass = "InitialBootPass123!"

    # Pre-clean test records as per AGENTS.md
    await neo4j_client.connect()
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    mock_settings = Settings(
        INITIAL_ADMIN_EMAIL=admin_email,
        INITIAL_ADMIN_PASSWORD=SecretStr(admin_pass),
        INITIAL_ADMIN_USERNAME=f"boot_admin_{uuid.uuid4().hex[:4]}",
        INITIAL_ADMIN_DISPLAY_NAME="Bootstrapped Administrator",
        FORCE_ADMIN_PASSWORD_RESET=False,
    )

    with patch("gosmart_graphite.service.user_service.get_settings", return_value=mock_settings):
        created_admin = await user_service.bootstrap_admin_user(test_id=test_id)

    assert created_admin is not None
    assert created_admin["primary_email"] == admin_email
    assert "PLATFORM_ADMIN" in created_admin["system_roles"]

    # Verify authentication works with the bootstrap password
    auth_user = await user_service.authenticate_local(
        identifier=admin_email,
        password=admin_pass,
    )
    assert auth_user is not None
    assert auth_user["id"] == created_admin["id"]

    # Verify admin is affiliated with Catch-All group
    catch_all = await group_service.get_or_create_catch_all_group(test_id=test_id)
    user_groups = await group_service.get_user_affiliations(created_admin["id"])
    assert any(g["group_id"] == catch_all["id"] for g in user_groups)


@pytest.mark.anyio
async def test_bootstrap_admin_idempotency():
    """Verify bootstrap is idempotent and does not overwrite password on normal restart."""
    test_id = f"test_boot_idem_{uuid.uuid4().hex[:6]}"
    admin_email = f"idem_admin_{uuid.uuid4().hex[:6]}@gosmart.health"
    pass_v1 = "PasswordVersion1!"
    pass_v2_ignored = "DifferentPassword2!"

    await neo4j_client.connect()
    await graph_service.cleanup_test_data(test_id)

    mock_settings_1 = Settings(
        INITIAL_ADMIN_EMAIL=admin_email,
        INITIAL_ADMIN_PASSWORD=SecretStr(pass_v1),
        INITIAL_ADMIN_USERNAME=f"idem_admin_{uuid.uuid4().hex[:4]}",
        FORCE_ADMIN_PASSWORD_RESET=False,
    )

    with patch("gosmart_graphite.service.user_service.get_settings", return_value=mock_settings_1):
        first_run = await user_service.bootstrap_admin_user(test_id=test_id)

    assert first_run is not None

    # Second startup with different env password but FORCE_ADMIN_PASSWORD_RESET=False
    mock_settings_2 = Settings(
        INITIAL_ADMIN_EMAIL=admin_email,
        INITIAL_ADMIN_PASSWORD=SecretStr(pass_v2_ignored),
        FORCE_ADMIN_PASSWORD_RESET=False,
    )

    with patch("gosmart_graphite.service.user_service.get_settings", return_value=mock_settings_2):
        second_run = await user_service.bootstrap_admin_user(test_id=test_id)

    assert second_run["id"] == first_run["id"]

    # Original password remains valid; second password is not used
    assert await user_service.authenticate_local(identifier=admin_email, password=pass_v1) is not None
    assert await user_service.authenticate_local(identifier=admin_email, password=pass_v2_ignored) is None


@pytest.mark.anyio
async def test_bootstrap_force_password_reset():
    """Verify FORCE_ADMIN_PASSWORD_RESET=True forces password update upon startup."""
    test_id = f"test_boot_force_{uuid.uuid4().hex[:6]}"
    admin_email = f"force_admin_{uuid.uuid4().hex[:6]}@gosmart.health"
    old_pass = "OldPassword123!"
    new_forced_pass = "NewEmergencyPassword456!"

    await neo4j_client.connect()
    await graph_service.cleanup_test_data(test_id)

    # 1. Create admin initially
    mock_settings_initial = Settings(
        INITIAL_ADMIN_EMAIL=admin_email,
        INITIAL_ADMIN_PASSWORD=SecretStr(old_pass),
        INITIAL_ADMIN_USERNAME=f"force_admin_{uuid.uuid4().hex[:4]}",
        FORCE_ADMIN_PASSWORD_RESET=False,
    )
    with patch("gosmart_graphite.service.user_service.get_settings", return_value=mock_settings_initial):
        admin = await user_service.bootstrap_admin_user(test_id=test_id)
    assert admin is not None

    # 2. Restart with FORCE_ADMIN_PASSWORD_RESET=True
    mock_settings_forced = Settings(
        INITIAL_ADMIN_EMAIL=admin_email,
        INITIAL_ADMIN_PASSWORD=SecretStr(new_forced_pass),
        FORCE_ADMIN_PASSWORD_RESET=True,
    )
    with patch("gosmart_graphite.service.user_service.get_settings", return_value=mock_settings_forced):
        updated_admin = await user_service.bootstrap_admin_user(test_id=test_id)

    assert updated_admin["id"] == admin["id"]

    # 3. Old password is invalid, new forced password succeeds
    assert await user_service.authenticate_local(identifier=admin_email, password=old_pass) is None
    auth_success = await user_service.authenticate_local(identifier=admin_email, password=new_forced_pass)
    assert auth_success is not None
    assert auth_success["id"] == admin["id"]


@pytest.mark.anyio
async def test_reset_admin_password_direct():
    """Verify reset_admin_password service function updates user password and works for login."""
    test_id = f"test_pwd_reset_{uuid.uuid4().hex[:6]}"
    email = f"user_pwd_{uuid.uuid4().hex[:6]}@gosmart.health"
    initial_pass = "InitialPass123!"
    new_pass = "UpdatedResetPass456!"

    await neo4j_client.connect()
    await graph_service.cleanup_test_data(test_id)

    user = await user_service.create_local_user(
        username=f"user_{uuid.uuid4().hex[:4]}",
        email=email,
        password=initial_pass,
        display_name="Password Test User",
        test_id=test_id,
        acted_by="test",
    )
    assert user is not None

    # Reset password
    success = await user_service.reset_admin_password(
        identifier=email,
        new_password=new_pass,
        acted_by="test_admin_reset",
        test_id=test_id,
    )
    assert success is True

    # Check that new password works and old fails
    assert await user_service.authenticate_local(identifier=email, password=initial_pass) is None
    authenticated = await user_service.authenticate_local(identifier=email, password=new_pass)
    assert authenticated is not None
    assert authenticated["id"] == user["id"]


@pytest.mark.anyio
async def test_cli_admin_commands(capsys):
    """Verify CLI admin subcommands (list-admins, reset-password, create-admin)."""
    cli_email = f"cli_admin_{uuid.uuid4().hex[:6]}@gosmart.health"
    cli_user = f"cli_user_{uuid.uuid4().hex[:4]}"
    cli_pass = "CliAdminPass123!"
    cli_reset_pass = "CliResetNewPass456!"

    await neo4j_client.connect()

    # 1. Create admin via CLI function
    res_create = await cmd_create_admin(
        username=cli_user,
        email=cli_email,
        password=cli_pass,
        display_name="CLI Created Admin",
    )
    assert res_create == 0

    # 2. List admins via CLI function
    res_list = await cmd_list_admins()
    assert res_list == 0
    captured = capsys.readouterr()
    assert cli_email in captured.out

    # 3. Reset password via CLI function
    res_reset = await cmd_reset_password(
        identifier=cli_email,
        password=cli_reset_pass,
    )
    assert res_reset == 0

    # 4. Verify login with reset password
    # Make sure client is connected for verification
    await neo4j_client.connect()
    auth = await user_service.authenticate_local(identifier=cli_email, password=cli_reset_pass)
    assert auth is not None
    assert auth["primary_email"] == cli_email
