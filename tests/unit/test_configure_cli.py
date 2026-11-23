"""Unit tests for configure CLI commands (dicom-config, fhir-config) and FHIRConfigService."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from gosmart_graphite.cli import cli_app
from gosmart_graphite.db.neo4j import Neo4jClient
from gosmart_graphite.model.dicom_config import DICOMConfigurationCreateRequest, DICOMConfigurationNode
from gosmart_graphite.model.fhir_config import (
    FHIRConfigurationCreateRequest,
    HasFhirConfigRelation,
)
from gosmart_graphite.model.graph import FHIRConfigurationNode
from gosmart_graphite.service.fhir_config_service import FHIRConfigService
from gosmart_graphite.service.system_service import SystemService


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


# ---------------------------------------------------------------------------
# 1. FHIR Config Domain & Request Model Tests
# ---------------------------------------------------------------------------


def test_fhir_config_request_and_relation_models() -> None:
    req = FHIRConfigurationCreateRequest.model_validate(
        {
            "url": "  https://fhir.example.org/r4  ",
            "accession_system": "  urn:oid:1.2.3.4  ",
            "patient_id_system": None,
            "poll_sec": "60",
            "group_id": "  grp_123  ",
            "test_id": "   ",
        }
    )
    assert req.url == "https://fhir.example.org/r4"
    assert req.accession_system == "urn:oid:1.2.3.4"
    assert req.patient_id_system == ""
    assert req.poll_sec == 60
    assert req.group_id == "grp_123"
    assert req.test_id is None

    rel = HasFhirConfigRelation(start_node_id="grp_123", end_node_id="fhir_123")
    assert rel.__type__ == "HAS_FHIR_CONFIG"
    assert rel.to_negated_type() == "NOT_HAS_FHIR_CONFIG"
    assert rel.id != ""

    with pytest.raises(ValidationError):
        FHIRConfigurationCreateRequest(url="   ")

    with pytest.raises(ValidationError, match="poll_sec"):
        FHIRConfigurationCreateRequest(url="https://fhir.example.org/r4", poll_sec=0)


# ---------------------------------------------------------------------------
# 2. FHIRConfigService Unit Tests (Mocked Neo4j)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fhir_config_service_resolve_target_group() -> None:
    mock_client = MagicMock()
    svc = FHIRConfigService(client=mock_client)

    # 1. Missing default 'GoSmart Health'
    mock_client.execute_query = AsyncMock(return_value=[])
    with pytest.raises(RuntimeError, match="GoSmart Health"):
        await svc.resolve_target_group(group_id=None)

    # 2. Missing explicit exact Group ID
    with pytest.raises(RuntimeError, match="Target group 'grp_missing' not found"):
        await svc.resolve_target_group(group_id="grp_missing")

    # 3. Ambiguous group match
    mock_client.execute_query = AsyncMock(
        return_value=[
            {"group": {"id": "grp_1", "name": "GoSmart Health", "code": "GOSMART_HEALTH"}},
            {"group": {"id": "grp_2", "name": "GoSmart Health", "code": "GOSMART_HEALTH_2"}},
        ]
    )
    with pytest.raises(RuntimeError, match="Ambiguous group match"):
        await svc.resolve_target_group(group_id=None)

    # 4. Single exact Group ID match
    mock_client.execute_query = AsyncMock(
        return_value=[{"group": {"id": "grp_exact", "name": "Clinic A", "code": "CLINIC_A"}}]
    )
    grp = await svc.resolve_target_group(group_id="grp_exact")
    assert grp["id"] == "grp_exact"


@pytest.mark.anyio
async def test_fhir_config_service_create_links_group_without_system() -> None:
    mock_client = MagicMock()
    svc = FHIRConfigService(client=mock_client)

    target_grp = {"id": "grp_exact", "name": "Clinic A", "code": "CLINIC_A", "group_type": "FACILITY"}
    mock_client.execute_query = AsyncMock(return_value=[{"group": target_grp}])

    expected_node = FHIRConfigurationNode(
        url="https://fhir.example.org/r4",
        accession_system="urn:oid:1.2.3.4",
        patient_id_system="urn:oid:2.16.840.1.113883.4.3.25",
        poll_sec=90,
    )
    fake_tx = _FakeAsyncTx(
        [
            _FakeAsyncResult(
                {
                    "config": {
                        "id": expected_node.id,
                        "url": "https://fhir.example.org/r4",
                        "accession_system": "urn:oid:1.2.3.4",
                        "patient_id_system": "urn:oid:2.16.840.1.113883.4.3.25",
                        "poll_sec": 90,
                        "Action": "CREATE",
                    },
                    "groups": [target_grp],
                }
            )
        ]
    )

    async def _run_write_tx(fn):
        return await fn(fake_tx)

    mock_client.execute_write_tx = AsyncMock(side_effect=_run_write_tx)

    with patch("gosmart_graphite.service.fhir_config_service.record_audit") as mock_audit:
        saved = await svc.create_fhir_config(
            config_data={
                "url": "https://fhir.example.org/r4",
                "accession_system": "urn:oid:1.2.3.4",
                "patient_id_system": "urn:oid:2.16.840.1.113883.4.3.25",
                "poll_sec": 90,
            },
            assign_group_id="grp_exact",
            acted_by="cli:configure",
        )

    assert len(fake_tx.calls) == 1
    cypher, kwargs = fake_tx.calls[0]
    assert "MERGE (f:FHIRConfiguration {id: $props.id})" in cypher
    assert "MERGE (tg)-[r:HAS_FHIR_CONFIG {id: $rel_id}]->(f)" in cypher
    assert "System" not in cypher
    assert kwargs["target_group_id"] == "grp_exact"
    assert saved["id"] == expected_node.id
    assert saved["group_ids"] == ["grp_exact"]
    assert saved["poll_sec"] == 90
    mock_audit.assert_called_once()


# ---------------------------------------------------------------------------
# 3. CLI Tests (configure dicom-config & configure fhir-config)
# ---------------------------------------------------------------------------


def test_cli_configure_dicom_config_validation_and_create(tmp_path: Path) -> None:
    runner = CliRunner()

    # 1. Missing file
    res_missing = runner.invoke(cli_app, ["configure", "dicom-config", "--file", str(tmp_path / "missing.json")])
    assert res_missing.exit_code == 1
    assert "not found" in res_missing.output.lower()

    # 2. Malformed JSON
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    res_bad_json = runner.invoke(cli_app, ["configure", "dicom-config", "--file", str(bad_json)])
    assert res_bad_json.exit_code == 1
    assert "failed to parse json" in res_bad_json.output.lower()

    # 3. JSON array rejected
    array_file = tmp_path / "array.json"
    array_file.write_text(json.dumps([{"ae_title": "ORTHANC", "port": 4242}]), encoding="utf-8")
    res_array = runner.invoke(cli_app, ["configure", "dicom-config", "--file", str(array_file)])
    assert res_array.exit_code == 1
    assert "single json object" in " ".join(res_array.output.lower().split())

    # 4. Invalid payload
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text(json.dumps({"ae_title": "CT_SCANNER", "port": "not-a-port"}), encoding="utf-8")
    res_invalid = runner.invoke(cli_app, ["configure", "dicom-config", "--file", str(invalid_file)])
    assert res_invalid.exit_code == 1
    assert "invalid dicomconfiguration payload" in " ".join(res_invalid.output.lower().split())

    # 5. Valid file, service failure exits 1
    valid_file = tmp_path / "valid_dicom.json"
    valid_file.write_text(
        json.dumps(
            {
                "ae_title": "ORTHANC",
                "roles": ["SCU"],
                "host": "10.0.0.10",
                "port": 4242,
                "is_local": False,
                "description": "Remote Orthanc PACS",
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("gosmart_graphite.cli.configure._ensure_cli_db_ready", new=AsyncMock()),
        patch.object(
            SystemService,
            "create_configuration",
            new=AsyncMock(side_effect=RuntimeError("Neo4j unreachable")),
        ),
        patch.object(Neo4jClient, "close", new=AsyncMock()),
    ):
        res_err = runner.invoke(cli_app, ["configure", "dicom-config", "--file", str(valid_file)])
        assert res_err.exit_code == 1
        assert "neo4j unreachable" in " ".join(res_err.output.lower().split())

    # 6. Valid creation succeeds and prints Rich summary table
    expected_id = DICOMConfigurationNode(ae_title="ORTHANC", host="10.0.0.10", port=4242).id
    saved_response = {
        "id": expected_id,
        "ae_title": "ORTHANC",
        "roles": ["SCU"],
        "host": "10.0.0.10",
        "port": 4242,
        "is_local": False,
        "is_promiscuous": False,
        "tls_enabled": False,
        "dicom_services": ["*"],
        "transfer_syntaxes": ["*"],
        "dicomweb_enabled": True,
        "dicomweb_url_prefix": "/dicomweb",
        "description": "Remote Orthanc PACS",
        "group_id": "grp_123",
        "test_id": "test_cli_dicom",
        "action": "CREATE",
    }

    with (
        patch("gosmart_graphite.cli.configure._ensure_cli_db_ready", new=AsyncMock()) as mock_ready,
        patch.object(
            SystemService,
            "create_configuration",
            new=AsyncMock(return_value=saved_response),
        ) as mock_create,
        patch.object(Neo4jClient, "close", new=AsyncMock()) as mock_close,
    ):
        res_ok = runner.invoke(
            cli_app,
            [
                "configure",
                "dicom-config",
                "--file",
                str(valid_file),
                "--assign",
                "grp_123",
                "--acted-by",
                "cli:tester",
                "--test-id",
                "test_cli_dicom",
            ],
        )
        assert res_ok.exit_code == 0, res_ok.output
        assert "ORTHANC" in res_ok.output
        assert "10.0.0.10" in res_ok.output
        assert "grp_123" in res_ok.output
        assert "test_cli_dicom" in res_ok.output
        mock_ready.assert_awaited_once_with(assign_group="grp_123", acted_by="cli:tester", test_id="test_cli_dicom")
        mock_create.assert_awaited_once()
        req_arg = mock_create.call_args.kwargs["config_data"]
        assert isinstance(req_arg, DICOMConfigurationCreateRequest)
        assert req_arg.group_id == "grp_123"
        assert req_arg.test_id == "test_cli_dicom"
        assert mock_create.call_args.kwargs["acted_by"] == "cli:tester"
        mock_close.assert_awaited_once()


def test_cli_configure_fhir_config_validation_and_create(tmp_path: Path) -> None:
    runner = CliRunner()

    # 1. Missing file
    res_missing = runner.invoke(cli_app, ["configure", "fhir-config", "--file", str(tmp_path / "missing.json")])
    assert res_missing.exit_code == 1
    assert "not found" in res_missing.output.lower()

    # 2. JSON array rejected
    array_file = tmp_path / "array.json"
    array_file.write_text(json.dumps([{"url": "https://fhir.example.org/r4"}]), encoding="utf-8")
    res_array = runner.invoke(cli_app, ["configure", "fhir-config", "--file", str(array_file)])
    assert res_array.exit_code == 1
    assert "single json object" in " ".join(res_array.output.lower().split())

    # 3. Invalid payload (missing url / invalid poll_sec)
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text(json.dumps({"url": "", "poll_sec": -5}), encoding="utf-8")
    res_invalid = runner.invoke(cli_app, ["configure", "fhir-config", "--file", str(invalid_file)])
    assert res_invalid.exit_code == 1
    assert "invalid fhirconfiguration payload" in " ".join(res_invalid.output.lower().split())

    # 4. Valid JSON, missing default GoSmart Health group exits 1
    valid_file = tmp_path / "valid_fhir.json"
    valid_file.write_text(
        json.dumps(
            {
                "url": "https://fhir.example.org/r4",
                "accession_system": "urn:oid:1.2.3.4",
                "patient_id_system": "urn:oid:2.16.840.1.113883.4.3.25",
                "poll_sec": 60,
            }
        ),
        encoding="utf-8",
    )

    with (
        patch("gosmart_graphite.cli.configure._ensure_cli_db_ready", new=AsyncMock()),
        patch.object(
            FHIRConfigService,
            "create_fhir_config",
            new=AsyncMock(side_effect=RuntimeError("Default group 'GoSmart Health' not found in Neo4j.")),
        ),
        patch.object(Neo4jClient, "close", new=AsyncMock()),
    ):
        res_missing_grp = runner.invoke(cli_app, ["configure", "fhir-config", "--file", str(valid_file)])
        assert res_missing_grp.exit_code == 1
        assert "gosmart health" in " ".join(res_missing_grp.output.lower().split())

    # 5. Valid creation succeeds and prints Rich summary table
    expected_id = FHIRConfigurationNode(url="https://fhir.example.org/r4").id
    saved_response = {
        "id": expected_id,
        "url": "https://fhir.example.org/r4",
        "accession_system": "urn:oid:1.2.3.4",
        "patient_id_system": "urn:oid:2.16.840.1.113883.4.3.25",
        "poll_sec": 60,
        "group_ids": ["grp_root"],
        "groups": [{"id": "grp_root", "name": "GoSmart Health", "code": "GOSMART_HEALTH"}],
        "test_id": "test_cli_fhir",
        "action": "CREATE",
    }

    with (
        patch("gosmart_graphite.cli.configure._ensure_cli_db_ready", new=AsyncMock()) as mock_ready,
        patch.object(
            FHIRConfigService,
            "create_fhir_config",
            new=AsyncMock(return_value=saved_response),
        ) as mock_create,
        patch.object(Neo4jClient, "close", new=AsyncMock()) as mock_close,
    ):
        res_ok = runner.invoke(
            cli_app,
            [
                "configure",
                "fhir-config",
                "--file",
                str(valid_file),
                "--assign",
                "grp_root",
                "--acted-by",
                "cli:tester",
                "--test-id",
                "test_cli_fhir",
            ],
        )
        assert res_ok.exit_code == 0, res_ok.output
        assert "https://fhir.example.org/r4" in res_ok.output
        assert "GoSmart Health (grp_root)" in res_ok.output
        assert "test_cli_fhir" in res_ok.output
        mock_ready.assert_awaited_once_with(assign_group="grp_root", acted_by="cli:tester", test_id="test_cli_fhir")
        mock_create.assert_awaited_once()
        assert mock_create.call_args.kwargs["assign_group_id"] == "grp_root"
        assert mock_create.call_args.kwargs["acted_by"] == "cli:tester"
        assert mock_create.call_args.kwargs["test_id"] == "test_cli_fhir"
        mock_close.assert_awaited_once()


@pytest.mark.anyio
async def test_ensure_cli_db_ready_initializes_schema_and_bootstraps_fresh_db() -> None:
    from gosmart_graphite.cli.configure import _ensure_cli_db_ready
    from gosmart_graphite.service.graph_service import graph_service
    from gosmart_graphite.service.group_service import group_service
    from gosmart_graphite.service.system_service import system_service

    # 1. Missing default 'GoSmart Health' triggers bootstrap_system after init_schema_constraints
    with (
        patch.object(graph_service, "init_schema_constraints", new=AsyncMock()) as mock_init,
        patch.object(
            group_service,
            "resolve_target_group",
            new=AsyncMock(side_effect=RuntimeError("Target group 'GoSmart Health' not found in Neo4j.")),
        ) as mock_resolve,
        patch.object(system_service, "bootstrap_system", new=AsyncMock(return_value={})) as mock_boot,
    ):
        await _ensure_cli_db_ready(assign_group="GoSmart Health", acted_by="cli:configure", test_id=None)
        mock_init.assert_awaited_once()
        mock_resolve.assert_awaited_once_with(identifier="GoSmart Health", test_id=None)
        mock_boot.assert_awaited_once_with(acted_by="cli:configure", test_id=None)

    # 2. Existing default group skips bootstrap_system
    with (
        patch.object(graph_service, "init_schema_constraints", new=AsyncMock()) as mock_init,
        patch.object(
            group_service,
            "resolve_target_group",
            new=AsyncMock(return_value={"id": "grp_default", "name": "GoSmart Health"}),
        ),
        patch.object(system_service, "bootstrap_system", new=AsyncMock()) as mock_boot,
    ):
        await _ensure_cli_db_ready(assign_group=None, acted_by="cli:configure", test_id=None)
        mock_init.assert_awaited_once()
        mock_boot.assert_not_awaited()

    # 3. Explicit custom group only initializes schema constraints (does not bootstrap default group)
    with (
        patch.object(graph_service, "init_schema_constraints", new=AsyncMock()) as mock_init,
        patch.object(group_service, "resolve_target_group", new=AsyncMock()) as mock_resolve,
        patch.object(system_service, "bootstrap_system", new=AsyncMock()) as mock_boot,
    ):
        await _ensure_cli_db_ready(assign_group="grp_custom", acted_by="cli:configure", test_id=None)
        mock_init.assert_awaited_once()
        mock_resolve.assert_not_awaited()
        mock_boot.assert_not_awaited()


@pytest.mark.anyio
async def test_init_schema_constraints_registers_config_relationship_indexes() -> None:
    from gosmart_graphite.service.graph_service import GraphService
    from gosmart_graphite.service.group_service import GroupService

    mock_client = MagicMock()
    mock_client.verify_connectivity = AsyncMock(return_value=True)
    mock_client.execute_query = AsyncMock(return_value=[])
    svc = GraphService(client=mock_client)

    with patch.object(GroupService, "ensure_lost_and_found_group", new=AsyncMock()):
        await svc.init_schema_constraints(force=True)

    statements = [call.args[0] for call in mock_client.execute_query.call_args_list]
    assert (
        "CREATE CONSTRAINT llm_config_sys_id_unique IF NOT EXISTS FOR (l:LLM_Config) REQUIRE l.id IS UNIQUE"
        in statements
    )
    assert "CREATE INDEX has_llm_config_id_idx IF NOT EXISTS FOR ()-[r:HAS_LLM_CONFIG]-() ON (r.id)" in statements
    assert (
        "CREATE INDEX not_has_llm_config_id_idx IF NOT EXISTS FOR ()-[r:NOT_HAS_LLM_CONFIG]-() ON (r.id)" in statements
    )
    assert "CREATE INDEX has_fhir_config_id_idx IF NOT EXISTS FOR ()-[r:HAS_FHIR_CONFIG]-() ON (r.id)" in statements
    assert (
        "CREATE INDEX has_dicom_config_id_idx IF NOT EXISTS FOR ()-[r:HAS_DICOM_CONFIGURATION]-() ON (r.id)"
        in statements
    )
    assert "CREATE INDEX serves_id_idx IF NOT EXISTS FOR ()-[r:SERVES]-() ON (r.id)" in statements
    assert "CREATE INDEX belongs_to_id_idx IF NOT EXISTS FOR ()-[r:BELONGS_TO]-() ON (r.id)" in statements
    assert "CREATE INDEX not_belongs_to_id_idx IF NOT EXISTS FOR ()-[r:NOT_BELONGS_TO]-() ON (r.id)" in statements


def test_cli_configure_llm_config_ensures_db_ready_and_upsert(tmp_path: Path) -> None:
    from gosmart_graphite.db.neo4j import neo4j_client
    from gosmart_graphite.service.llm_config_service import llm_config_service

    runner = CliRunner()
    llm_file = tmp_path / "llm_embedding.json"
    llm_file.write_text(
        json.dumps(
            {
                "tag": "embedding-v1",
                "provider": "ollama",
                "model": "embeddinggemma",
                "url": "http://localhost:11434",
                "dimensions": 768,
            }
        ),
        encoding="utf-8",
    )
    saved_response = {
        "id": "llm_cfg_123",
        "tag": "embedding-v1",
        "provider": "ollama",
        "model": "embeddinggemma",
        "url": "http://localhost:11434",
        "dimensions": 768,
        "dimensions_truncated": None,
        "api_key": None,
        "api_key_source": "none",
        "location": "us-central1",
        "project_id": None,
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
        patch.object(neo4j_client, "close", new=AsyncMock()) as mock_close,
    ):
        res = runner.invoke(
            cli_app,
            ["configure", "llm-config", "--file", str(llm_file), "--assign", "GoSmart Health"],
        )
        assert res.exit_code == 0, res.output
        assert "embedding-v1" in res.output
        assert "GoSmart Health (grp_root)" in res.output
        mock_ready.assert_awaited_once_with(assign_group="GoSmart Health", acted_by="cli:configure", test_id=None)
        mock_upsert.assert_awaited_once()
        mock_close.assert_awaited_once()


# ---------------------------------------------------------------------------
# 4. Template JSON Validation Tests
# ---------------------------------------------------------------------------


def test_devop_template_json_files_valid() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dicom_tpl = json.loads((repo_root / "devop-scripts" / "dicom_config.template.json").read_text(encoding="utf-8"))
    fhir_tpl = json.loads((repo_root / "devop-scripts" / "fhir_config.template.json").read_text(encoding="utf-8"))

    dicom_req = DICOMConfigurationCreateRequest(**dicom_tpl)
    fhir_req = FHIRConfigurationCreateRequest(**fhir_tpl)

    assert dicom_req.ae_title == "GOSMART_GS"
    assert fhir_req.url == "http://localhost:8080/fhir"
    assert fhir_req.poll_sec == 120
