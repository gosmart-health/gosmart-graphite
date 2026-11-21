"""Unit tests for dynamic group-scoped LLM configuration models and interfaces."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from gosmart_graphite.cli import cli_app
from gosmart_graphite.core.auth_dependency import (
    get_active_group_id,
    get_current_group_context,
    get_current_user,
    reset_active_group_id,
    set_active_group_id,
)
from gosmart_graphite.core.security import (
    classify_llm_api_key,
    encrypt_llm_api_key,
    is_env_var_reference,
    mask_llm_api_key,
    resolve_llm_api_key,
)
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.main import app
from gosmart_graphite.model.llm_config import LLM_Config
from gosmart_graphite.service.llm_config_service import LLMConfigService, llm_config_service
from gosmart_graphite.service.mcp_service import (
    MCPAuthMiddleware,
    _current_mcp_user,
    assign_llm_configuration_to_group,
    delete_llm_configuration,
    dissociate_llm_configuration_from_group,
    get_llm_configuration,
    list_llm_configurations,
    restore_llm_configuration,
    upsert_llm_configuration,
)

# ---------------------------------------------------------------------------
# 1. Domain Model & Encryption / Masking Tests
# ---------------------------------------------------------------------------


def test_llm_config_deterministic_id_and_validation() -> None:
    cfg1 = LLM_Config(
        tag="embedding",
        provider="Ollama",
        model="nomic-embed-text:latest",
        url="http://localhost:11434",
        dimensions=768,
    )
    cfg2 = LLM_Config(
        tag="embedding",
        provider="VertexAI",
        model="gemini-embedding-001",
        dimensions=1536,
        dimensions_truncated=768,
        project_id="proj-1",
    )
    cfg_other_tag = LLM_Config(
        tag="router",
        provider="vertexai",
        model="gemini-2.5-flash",
        extra_config={"temperature": 0.1},
    )
    cfg_test_scoped = LLM_Config(
        tag="embedding",
        provider="ollama",
        model="nomic-embed-text:latest",
        test_id="test_llm_1",
    )

    assert cfg1.provider == "ollama"
    assert cfg2.provider == "vertexai"
    assert cfg1.id == cfg2.id
    assert cfg1.id != cfg_other_tag.id
    assert cfg1.id != cfg_test_scoped.id

    # Group-scoped deterministic IDs differ across sponsoring groups for the same tag
    scoped_grp_a = LLM_Config.compute_group_scoped_id(tag="embedding", group_id="grp_a")
    scoped_grp_b = LLM_Config.compute_group_scoped_id(tag="embedding", group_id="grp_b")
    scoped_grp_a_dup = LLM_Config.compute_group_scoped_id(tag="embedding", group_id="grp_a")
    scoped_grp_a_test = LLM_Config.compute_group_scoped_id(tag="embedding", group_id="grp_a", test_id="test_llm_1")
    assert scoped_grp_a == scoped_grp_a_dup
    assert scoped_grp_a != scoped_grp_b
    assert scoped_grp_a != scoped_grp_a_test
    assert scoped_grp_a != cfg1.id

    # Optional fields for chat/routing LLMs
    assert cfg_other_tag.url == ""
    assert cfg_other_tag.dimensions is None
    assert cfg_other_tag.dimensions_truncated is None
    props = cfg_other_tag.to_neo4j_properties()
    assert json.loads(props["extra_config"]) == {"temperature": 0.1}

    # Invalid truncated dimensions > dimensions
    with pytest.raises(ValidationError, match="dimensions_truncated"):
        LLM_Config(
            tag="embedding",
            provider="vertexai",
            model="gemini-embedding-001",
            dimensions=256,
            dimensions_truncated=512,
        )


def test_llm_api_key_encryption_env_ref_and_masking() -> None:
    assert is_env_var_reference("${GOSMART_GS_VERTEX_AI_API_KEY}") is True
    assert is_env_var_reference("AIzaSyPlainSecretKey9999") is False

    # Env var reference is preserved verbatim even when encryption key is set
    stored_env_ref = encrypt_llm_api_key("${MY_SECRET_KEY_VAR}", encryption_key="passphrase-123")
    assert stored_env_ref == "${MY_SECRET_KEY_VAR}"
    assert classify_llm_api_key(stored_env_ref) == "env"
    assert mask_llm_api_key(stored_env_ref) == "${MY_SECRET_KEY_VAR}"

    with patch.dict(os.environ, {"MY_SECRET_KEY_VAR": "resolved-secret-val"}, clear=False):
        assert resolve_llm_api_key(stored_env_ref, encryption_key="passphrase-123") == "resolved-secret-val"

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="MY_SECRET_KEY_VAR"):
            resolve_llm_api_key("${MY_SECRET_KEY_VAR}")

    # Literal key encrypted at rest via Fernet
    encrypted = encrypt_llm_api_key("AIzaSySuperSecret1234", encryption_key="unit-test-encryption-secret")
    assert encrypted.startswith("enc:fernet:")
    assert "SuperSecret" not in encrypted
    assert classify_llm_api_key(encrypted) == "encrypted"
    assert mask_llm_api_key(encrypted) == "enc:fernet:********"
    assert resolve_llm_api_key(encrypted, encryption_key="unit-test-encryption-secret") == "AIzaSySuperSecret1234"

    # Plaintext fallback masking
    assert classify_llm_api_key("sk-1234567890abcdef") == "plaintext"
    masked = mask_llm_api_key("sk-1234567890abcdef")
    assert "1234567890ab" not in masked
    assert masked.endswith("cdef")


# ---------------------------------------------------------------------------
# 2. LLMConfigService Unit Tests (Mocked Neo4j)
# ---------------------------------------------------------------------------


class _FakeAsyncResult:
    def __init__(self, record=None) -> None:
        self._record = record

    async def single(self):
        return self._record


class _FakeAsyncTx:
    def __init__(self, scripted_runs) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._scripted = iter(scripted_runs)

    async def run(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        return next(self._scripted, _FakeAsyncResult(None))


@pytest.mark.anyio
async def test_resolve_target_group_errors_and_success() -> None:
    mock_client = MagicMock()
    svc = LLMConfigService(client=mock_client)

    # 1. Missing default 'GoSmart Health'
    mock_client.execute_query = AsyncMock(return_value=[])
    with pytest.raises(RuntimeError, match="GoSmart Health"):
        await svc.resolve_target_group(identifier=None)

    # 2. Ambiguous --assign (>1 matches)
    mock_client.execute_query = AsyncMock(
        return_value=[
            {"group": {"id": "grp_1", "name": "Clinic", "code": "CLINIC_1"}},
            {"group": {"id": "grp_2", "name": "Clinic", "code": "CLINIC_2"}},
        ]
    )
    with pytest.raises(RuntimeError, match="Ambiguous group match"):
        await svc.resolve_target_group(identifier="Clinic")

    # 3. Single match
    mock_client.execute_query = AsyncMock(
        return_value=[{"group": {"id": "grp_root", "name": "GoSmart Health", "code": "GOSMART_HEALTH"}}]
    )
    grp = await svc.resolve_target_group(identifier=None)
    assert grp["id"] == "grp_root"


@pytest.mark.anyio
async def test_upsert_overwrite_clones_archive_and_preserves_groups() -> None:
    mock_client = MagicMock()
    svc = LLMConfigService(client=mock_client)

    target_grp = {"id": "grp_b", "name": "Clinic B", "code": "CLINIC_B"}
    mock_client.execute_query = AsyncMock(return_value=[{"group": target_grp}])

    existing_cfg_id = LLM_Config.compute_group_scoped_id(tag="embedding", group_id="grp_b")
    fake_tx = _FakeAsyncTx(
        [
            # 1. Check existing node & active groups (grp_a and grp_b)
            _FakeAsyncResult(
                {
                    "grp_existing": {
                        "id": existing_cfg_id,
                        "tag": "embedding",
                        "provider": "ollama",
                        "model": "old-model",
                        "Action": "CREATE",
                    },
                    "grp_existing_group_ids": ["grp_a", "grp_b"],
                    "scoped_existing": None,
                    "scoped_group_ids": [],
                }
            ),
            # 2. Archive clone + NOT_HAS_LLM_CONFIG links
            _FakeAsyncResult(None),
            # 3. Upsert canonical node + HAS_LLM_CONFIG link returning both active groups
            _FakeAsyncResult(
                {
                    "config": {
                        "id": existing_cfg_id,
                        "tag": "embedding",
                        "provider": "vertexai",
                        "model": "gemini-embedding-001",
                        "api_key": "AIzaSyTopSecret9999",
                        "Action": "UPDATE",
                    },
                    "groups": [
                        {"id": "grp_a", "name": "GoSmart Health", "code": "GOSMART_HEALTH"},
                        {"id": "grp_b", "name": "Clinic B", "code": "CLINIC_B"},
                    ],
                }
            ),
        ]
    )

    async def _run_write_tx(fn):
        return await fn(fake_tx)

    mock_client.execute_write_tx = AsyncMock(side_effect=_run_write_tx)

    with patch("gosmart_graphite.service.llm_config_service.record_audit"):
        res = await svc.upsert_llm_config(
            config_data={
                "tag": "embedding",
                "provider": "vertexai",
                "model": "gemini-embedding-001",
                "api_key": "AIzaSyTopSecret9999",
            },
            assign_group="grp_b",
            acted_by="tester",
        )

    assert len(fake_tx.calls) == 3
    archive_query, archive_kwargs = fake_tx.calls[1]
    assert "CREATE (arch:LLM_Config)" in archive_query
    assert "NOT_HAS_LLM_CONFIG" in archive_query
    assert archive_kwargs["archive_id"].startswith(f"{existing_cfg_id}__archived__")
    assert len(archive_kwargs["neg_links"]) == 2

    # Sanitized response masks raw secret and retains all associated groups
    assert res["api_key"] != "AIzaSyTopSecret9999"
    assert res["has_api_key"] is True
    assert set(res["group_ids"]) == {"grp_a", "grp_b"}


@pytest.mark.anyio
async def test_upsert_shared_config_on_other_group_dissociates_and_creates_group_scoped() -> None:
    mock_client = MagicMock()
    svc = LLMConfigService(client=mock_client)

    target_grp = {"id": "grp_b", "name": "Clinic B", "code": "CLINIC_B"}
    mock_client.execute_query = AsyncMock(return_value=[{"group": target_grp}])

    shared_cfg_id = LLM_Config.compute_group_scoped_id(tag="embedding", group_id="grp_a")
    own_cfg_id = LLM_Config.compute_group_scoped_id(tag="embedding", group_id="grp_b")
    fake_tx = _FakeAsyncTx(
        [
            # 1. Inspect: grp_b currently shares grp_a's embedding config
            _FakeAsyncResult(
                {
                    "grp_existing": {
                        "id": shared_cfg_id,
                        "tag": "embedding",
                        "provider": "vertexai",
                        "model": "gemini-embedding-001",
                        "Action": "CREATE",
                    },
                    "grp_existing_group_ids": ["grp_a", "grp_b"],
                    "scoped_existing": None,
                    "scoped_group_ids": [],
                }
            ),
            # 2. Dissociate grp_b from shared_cfg_id
            _FakeAsyncResult(None),
            # 3. Create grp_b's own scoped node
            _FakeAsyncResult(
                {
                    "config": {
                        "id": own_cfg_id,
                        "tag": "embedding",
                        "provider": "vertexai",
                        "model": "gemini-embedding-001",
                        "api_key": "AIzaSyGroupBKey9999",
                        "Action": "CREATE",
                    },
                    "groups": [{"id": "grp_b", "name": "Clinic B", "code": "CLINIC_B"}],
                }
            ),
        ]
    )

    async def _run_write_tx(fn):
        return await fn(fake_tx)

    mock_client.execute_write_tx = AsyncMock(side_effect=_run_write_tx)

    with patch("gosmart_graphite.service.llm_config_service.record_audit"):
        res = await svc.upsert_llm_config(
            config_data={
                "tag": "embedding",
                "provider": "vertexai",
                "model": "gemini-embedding-001",
                "api_key": "AIzaSyGroupBKey9999",
            },
            assign_group="grp_b",
            acted_by="tester",
        )

    assert len(fake_tx.calls) == 3
    dissoc_query, dissoc_kwargs = fake_tx.calls[1]
    assert "NOT_HAS_LLM_CONFIG" in dissoc_query
    assert dissoc_kwargs["shared_cfg_id"] == shared_cfg_id
    assert res["id"] == own_cfg_id
    assert res["group_ids"] == ["grp_b"]


@pytest.mark.anyio
async def test_group_tag_uniqueness_enforced_on_assign_update_and_restore() -> None:
    mock_client = MagicMock()
    svc = LLMConfigService(client=mock_client)

    async def _run_write_tx(fn):
        return await fn(fake_tx)

    mock_client.execute_write_tx = AsyncMock(side_effect=_run_write_tx)

    # 1. assign_to_group rejects duplicate tag on target group
    svc.get_llm_config = AsyncMock(
        return_value={"id": "cfg_a", "tag": "embedding", "provider": "vertexai", "model": "gemini-embedding-001"}
    )
    svc.resolve_target_group = AsyncMock(return_value={"id": "grp_b", "name": "Clinic B", "code": "CLINIC_B"})
    fake_tx = _FakeAsyncTx([_FakeAsyncResult({"conflict_id": "cfg_b"})])
    with pytest.raises(ValueError, match="already has an active LLM_Config with tag 'embedding'"):
        await svc.assign_to_group(config_id="cfg_a", group_identifier="grp_b")
    assert len(fake_tx.calls) == 1

    # 2. update_llm_config with assign_group rejects duplicate tag before archiving
    mock_client.execute_query = AsyncMock(
        return_value=[
            {
                "config": {
                    "id": "cfg_a",
                    "tag": "embedding",
                    "provider": "vertexai",
                    "model": "gemini-embedding-001",
                    "api_key": "",
                },
                "groups": [{"id": "grp_a", "name": "Clinic A", "code": "CLINIC_A"}],
            }
        ]
    )
    from gosmart_graphite.model.llm_config import LLMConfigUpdateRequest

    fake_tx = _FakeAsyncTx([_FakeAsyncResult({"conflict_id": "cfg_b"})])
    with pytest.raises(ValueError, match="already has an active LLM_Config with tag 'embedding'"):
        await svc.update_llm_config(
            config_id="cfg_a",
            update_data=LLMConfigUpdateRequest(assign_group="grp_b"),
        )
    assert len(fake_tx.calls) == 1

    # 3. restore_llm_config rejects restoring link if group already has another active config with same tag
    fake_tx = _FakeAsyncTx(
        [
            _FakeAsyncResult({"id": "cfg_old", "tag": "embedding", "test_id": None, "group_ids": ["grp_b"]}),
            _FakeAsyncResult({"conflict_id": "cfg_b"}),
        ]
    )
    with pytest.raises(ValueError, match="Cannot restore LLM_Config 'cfg_old'"):
        await svc.restore_llm_config(config_id="cfg_old")


@pytest.mark.anyio
async def test_get_llm_config_by_tag_group_disambiguation() -> None:
    mock_client = MagicMock()
    svc = LLMConfigService(client=mock_client)

    rec_a = {
        "config": {"id": "cfg_a", "tag": "embedding", "provider": "vertexai", "model": "gemini-embedding-001"},
        "groups": [{"id": "grp_a", "name": "Clinic A", "code": "CLINIC_A"}],
    }
    rec_b = {
        "config": {"id": "cfg_b", "tag": "embedding", "provider": "vertexai", "model": "gemini-embedding-001"},
        "groups": [{"id": "grp_b", "name": "Clinic B", "code": "CLINIC_B"}],
    }

    # 1. Explicit group_id returns matching group's config
    mock_client.execute_query = AsyncMock(return_value=[rec_b])
    res_b = await svc.get_llm_config(tag="embedding", group_id="grp_b")
    assert res_b is not None
    assert res_b["id"] == "cfg_b"

    # 2. Ambiguous across groups with no group_id or active_group_id raises RuntimeError
    mock_client.execute_query = AsyncMock(return_value=[rec_a, rec_b])
    with pytest.raises(RuntimeError, match="Ambiguous LLM_Config for tag 'embedding'"):
        await svc.get_llm_config(tag="embedding")

    # 3. Active group context disambiguates when multiple configs share a tag
    token = set_active_group_id("grp_b")
    try:
        res_ctx = await svc.get_llm_config(tag="embedding")
    finally:
        reset_active_group_id(token)
    assert res_ctx is not None
    assert res_ctx["id"] == "cfg_b"


@pytest.mark.anyio
async def test_resolve_config_for_group_upward_and_default_fallback() -> None:
    mock_client = MagicMock()
    svc = LLMConfigService(client=mock_client)

    root_cfg_props = {
        "id": LLM_Config(tag="embedding", provider="ollama", model="root-model").id,
        "tag": "embedding",
        "provider": "ollama",
        "model": "root-model",
        "url": "http://root:11434",
    }
    child_cfg_props = {
        "id": LLM_Config(tag="embedding", provider="vertexai", model="gemini-embedding-001").id,
        "tag": "embedding",
        "provider": "vertexai",
        "model": "gemini-embedding-001",
    }

    # Case A: ancestor traversal returns child/parent config at min depth
    mock_client.execute_query = AsyncMock(
        return_value=[
            {"config": child_cfg_props, "group_id": "grp_child", "depth": 0},
            {"config": root_cfg_props, "group_id": "grp_root", "depth": 2},
        ]
    )
    resolved = await svc.resolve_config_for_group(tag="embedding", group_id="grp_child")
    assert resolved is not None
    assert resolved.provider == "vertexai"

    # Case B: no ancestor config -> falls back to 'GoSmart Health'
    mock_client.execute_query = AsyncMock(
        side_effect=[
            [],  # ancestor query
            [{"config": root_cfg_props, "group_id": "grp_default"}],  # default GoSmart Health query
        ]
    )
    token = set_active_group_id("grp_orphan")
    try:
        fallback_resolved = await svc.resolve_config_for_group(tag="embedding")
    finally:
        reset_active_group_id(token)
    assert fallback_resolved is not None
    assert fallback_resolved.model == "root-model"

    # Case C: ambiguous multiple configs at same depth raises RuntimeError
    mock_client.execute_query = AsyncMock(
        return_value=[
            {"config": {"id": "id_1", "tag": "embedding", "provider": "ollama", "model": "m1"}, "depth": 1},
            {"config": {"id": "id_2", "tag": "embedding", "provider": "vertexai", "model": "m2"}, "depth": 1},
        ]
    )
    with pytest.raises(RuntimeError, match="Ambiguous LLM_Config"):
        await svc.resolve_config_for_group(tag="embedding", group_id="grp_child")


@pytest.mark.anyio
async def test_restore_rejects_archived_id() -> None:
    svc = LLMConfigService(client=MagicMock())
    assert await svc.restore_llm_config("abc__archived__123456") is None


# ---------------------------------------------------------------------------
# 3. CLI Tests (configure llm-config)
# ---------------------------------------------------------------------------


def test_cli_configure_llm_config_validation_and_upsert(tmp_path: Path) -> None:
    runner = CliRunner()

    # 1. Missing file
    res_missing = runner.invoke(cli_app, ["configure", "llm-config", "--file", str(tmp_path / "missing.json")])
    assert res_missing.exit_code == 1
    assert "not found" in res_missing.output.lower()

    # 2. JSON array rejected
    array_file = tmp_path / "array.json"
    array_file.write_text(
        json.dumps([{"tag": "embedding", "provider": "ollama", "model": "nomic-embed-text:latest"}]),
        encoding="utf-8",
    )
    res_array = runner.invoke(cli_app, ["configure", "llm-config", "--file", str(array_file)])
    assert res_array.exit_code == 1
    assert "single json object" in res_array.output.lower()

    # 3. Valid single JSON object, missing GoSmart Health raises exit 1
    valid_file = tmp_path / "valid.json"
    valid_file.write_text(
        json.dumps(
            {
                "tag": "embedding",
                "provider": "vertexai",
                "model": "gemini-embedding-001",
                "api_key": "AIzaSyCliSecret9999",
                "dimensions": 768,
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("gosmart_graphite.cli.configure._ensure_cli_db_ready", new=AsyncMock()),
        patch.object(
            llm_config_service,
            "upsert_llm_config",
            new=AsyncMock(side_effect=RuntimeError("Default group 'GoSmart Health' not found in Neo4j.")),
        ),
        patch.object(neo4j_client, "close", new=AsyncMock()),
    ):
        res_missing_grp = runner.invoke(cli_app, ["configure", "llm-config", "--file", str(valid_file)])
        assert res_missing_grp.exit_code == 1
        assert "gosmart health" in res_missing_grp.output.lower()

    # 4. Valid upsert succeeds and masks API key in Rich summary output
    saved_response = {
        "id": LLM_Config(tag="embedding", provider="vertexai", model="gemini-embedding-001").id,
        "tag": "embedding",
        "provider": "vertexai",
        "model": "gemini-embedding-001",
        "url": "",
        "dimensions": 768,
        "dimensions_truncated": None,
        "api_key": "AIza****9999",
        "has_api_key": True,
        "api_key_source": "plaintext",
        "location": "us-central1",
        "project_id": None,
        "extra_config": None,
        "groups": [{"id": "grp_root", "name": "GoSmart Health", "code": "GOSMART_HEALTH"}],
        "action": "CREATE",
    }
    with (
        patch("gosmart_graphite.cli.configure._ensure_cli_db_ready", new=AsyncMock()) as mock_ready,
        patch.object(
            llm_config_service,
            "upsert_llm_config",
            new=AsyncMock(return_value=saved_response),
        ) as mock_upsert,
        patch.object(neo4j_client, "close", new=AsyncMock()),
    ):
        res_ok = runner.invoke(
            cli_app,
            ["configure", "llm-config", "--file", str(valid_file), "--assign", "GoSmart Health"],
        )
        assert res_ok.exit_code == 0, res_ok.output
        assert "AIzaSyCliSecret9999" not in res_ok.output
        assert "AIza****9999" in res_ok.output
        assert "gemini-embedding-001" in res_ok.output
        mock_ready.assert_awaited_once_with(assign_group="GoSmart Health", acted_by="cli:configure", test_id=None)
        mock_upsert.assert_awaited_once()
        assert mock_upsert.call_args.kwargs["assign_group"] == "GoSmart Health"


# ---------------------------------------------------------------------------
# 4. REST API Tests (/api/v1/configurations/llm)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(isolated_app_lifespan):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "usr_llm_admin",
        "primary_email": "llm-admin@gosmart.health",
        "system_roles": ["PLATFORM_ADMIN"],
        "is_active": True,
    }
    app.dependency_overrides[get_current_group_context] = lambda: {
        "id": "grp_default",
        "group_id": "grp_default",
        "name": "GoSmart Health",
    }
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def clinician_client(isolated_app_lifespan):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "usr_llm_clinician",
        "primary_email": "clinician@gosmart.health",
        "system_roles": ["CLINICIAN"],
        "is_active": True,
    }
    app.dependency_overrides[get_current_group_context] = lambda: {
        "id": "grp_clinic",
        "group_id": "grp_clinic",
        "roles": ["CLINICIAN"],
    }
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_llm_configuration_rbac_forbidden(clinician_client: TestClient) -> None:
    forbidden_res = clinician_client.post(
        "/api/v1/configurations/llm",
        json={"tag": "router", "provider": "vertexai", "model": "gemini-2.5-flash"},
    )
    assert forbidden_res.status_code == 403


def test_api_llm_configuration_crud(admin_client: TestClient) -> None:
    cfg_id = LLM_Config(tag="router", provider="vertexai", model="gemini-2.5-flash").id
    mock_resp = {
        "id": cfg_id,
        "tag": "router",
        "provider": "vertexai",
        "model": "gemini-2.5-flash",
        "url": "",
        "dimensions": None,
        "dimensions_truncated": None,
        "api_key": "${VERTEX_ROUTER_KEY}",
        "has_api_key": True,
        "api_key_source": "env",
        "location": "us-central1",
        "project_id": "proj-1",
        "extra_config": {"temperature": 0.1},
        "description": "GraphRAG query router",
        "group_ids": ["grp_default"],
        "groups": [{"id": "grp_default", "name": "GoSmart Health", "code": "GOSMART_HEALTH"}],
        "test_id": None,
        "action": "CREATE",
        "created_at": "2026-11-15T00:00:00Z",
        "updated_at": None,
    }

    # Create / upsert
    with patch.object(llm_config_service, "upsert_llm_config", new=AsyncMock(return_value=mock_resp)):
        res_post = admin_client.post(
            "/api/v1/configurations/llm",
            json={
                "tag": "router",
                "provider": "vertexai",
                "model": "gemini-2.5-flash",
                "api_key": "${VERTEX_ROUTER_KEY}",
                "extra_config": {"temperature": 0.1},
            },
        )
        assert res_post.status_code == 201
        assert res_post.json()["id"] == cfg_id
        assert res_post.json()["api_key"] == "${VERTEX_ROUTER_KEY}"

    # List
    with patch.object(llm_config_service, "list_llm_configs", new=AsyncMock(return_value=[mock_resp])):
        res_list = admin_client.get("/api/v1/configurations/llm?tag=router")
        assert res_list.status_code == 200
        assert len(res_list.json()) == 1

    # Get
    with patch.object(llm_config_service, "get_llm_config", new=AsyncMock(return_value=mock_resp)):
        res_get = admin_client.get(f"/api/v1/configurations/llm/{cfg_id}")
        assert res_get.status_code == 200
        assert res_get.json()["model"] == "gemini-2.5-flash"

    # Update
    updated_resp = {**mock_resp, "action": "UPDATE", "model": "gemini-2.5-pro"}
    with patch.object(llm_config_service, "update_llm_config", new=AsyncMock(return_value=updated_resp)):
        res_put = admin_client.put(f"/api/v1/configurations/llm/{cfg_id}", json={"model": "gemini-2.5-pro"})
        assert res_put.status_code == 200
        assert res_put.json()["model"] == "gemini-2.5-pro"

    # Assign
    assigned_resp = {**updated_resp, "group_ids": ["grp_default", "grp_clinic"]}
    with patch.object(llm_config_service, "assign_to_group", new=AsyncMock(return_value=assigned_resp)):
        res_assign = admin_client.post(f"/api/v1/configurations/llm/{cfg_id}/assign", json={"group": "grp_clinic"})
        assert res_assign.status_code == 200
        assert "grp_clinic" in res_assign.json()["group_ids"]

    # Dissociate
    with patch.object(llm_config_service, "dissociate_from_group", new=AsyncMock(return_value=True)):
        res_dissoc = admin_client.delete(f"/api/v1/configurations/llm/{cfg_id}/groups/grp_clinic")
        assert res_dissoc.status_code == 200
        assert res_dissoc.json()["success"] is True

    # Soft delete & Restore
    with patch.object(llm_config_service, "delete_llm_config", new=AsyncMock(return_value=True)):
        res_del = admin_client.delete(f"/api/v1/configurations/llm/{cfg_id}")
        assert res_del.status_code == 200
        assert res_del.json()["hard_deleted"] is False

    restored_resp = {**updated_resp, "action": "RESTORE"}
    with patch.object(llm_config_service, "restore_llm_config", new=AsyncMock(return_value=restored_resp)):
        res_restore = admin_client.post(f"/api/v1/configurations/llm/{cfg_id}/restore")
        assert res_restore.status_code == 200
        assert res_restore.json()["action"] == "RESTORE"


# ---------------------------------------------------------------------------
# 5. MCP Tools & Active Group Context Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_mcp_llm_configuration_tools_and_group_middleware() -> None:
    cfg_id = LLM_Config(tag="embedding", provider="ollama", model="nomic-embed-text:latest").id
    mock_cfg = {
        "id": cfg_id,
        "tag": "embedding",
        "provider": "ollama",
        "model": "nomic-embed-text:latest",
        "api_key": "",
        "group_ids": ["grp_root"],
    }

    with patch.object(llm_config_service, "upsert_llm_config", new=AsyncMock(return_value=mock_cfg)):
        saved = await upsert_llm_configuration(
            tag="embedding",
            provider="ollama",
            model="nomic-embed-text:latest",
            url="http://localhost:11434",
            extra_config_json='{"num_ctx": 4096}',
            acted_by="mcp_test",
        )
        assert saved["id"] == cfg_id

    with patch.object(llm_config_service, "list_llm_configs", new=AsyncMock(return_value=[mock_cfg])):
        listed = await list_llm_configurations(tag="embedding")
        assert len(listed) == 1

    with patch.object(llm_config_service, "get_llm_config", new=AsyncMock(return_value=mock_cfg)):
        fetched = await get_llm_configuration(tag="embedding")
        assert fetched is not None
        assert fetched["id"] == cfg_id

    with patch.object(llm_config_service, "assign_to_group", new=AsyncMock(return_value=mock_cfg)):
        assigned = await assign_llm_configuration_to_group(config_id=cfg_id, group="grp_root")
        assert assigned is not None

    with patch.object(llm_config_service, "dissociate_from_group", new=AsyncMock(return_value=True)):
        dissoc = await dissociate_llm_configuration_from_group(config_id=cfg_id, group_id="grp_root")
        assert dissoc["success"] is True

    with patch.object(llm_config_service, "delete_llm_config", new=AsyncMock(return_value=True)):
        deleted = await delete_llm_configuration(config_id=cfg_id)
        assert deleted["success"] is True

    with patch.object(llm_config_service, "restore_llm_config", new=AsyncMock(return_value=mock_cfg)):
        restored = await restore_llm_configuration(config_id=cfg_id)
        assert restored["success"] is True

    # Non-admin MCP user blocked from mutations
    token_user = _current_mcp_user.set({"id": "u_basic", "system_roles": ["CLINICIAN"]})
    try:
        with pytest.raises(PermissionError, match="Admin"):
            await upsert_llm_configuration(tag="embedding", provider="ollama", model="m")
    finally:
        _current_mcp_user.reset(token_user)

    # MCPAuthMiddleware binds X-Group-ID into active_group_id ContextVar during request
    observed_group_id = None

    async def _fake_app(scope, receive, send):
        nonlocal observed_group_id
        observed_group_id = get_active_group_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = MCPAuthMiddleware(_fake_app)
    with (
        patch("gosmart_graphite.service.mcp_service.decode_token", return_value={"sub": "usr_1", "roles": ["ADMIN"]}),
        patch(
            "gosmart_graphite.service.mcp_service.user_service.get_user_by_id",
            new=AsyncMock(
                return_value={
                    "id": "usr_1",
                    "primary_email": "a@b.com",
                    "system_roles": ["ADMIN"],
                    "is_active": True,
                }
            ),
        ),
    ):
        scope = {
            "type": "http",
            "method": "GET",
            "headers": [
                (b"authorization", b"Bearer testtoken"),
                (b"x-group-id", b"grp_custom_mcp"),
            ],
            "query_string": b"",
        }
        await mw(scope, AsyncMock(), AsyncMock())

    assert observed_group_id == "grp_custom_mcp"
    assert get_active_group_id() is None
