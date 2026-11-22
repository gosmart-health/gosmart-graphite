from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from gosmart_graphite.core.auth_dependency import get_current_group_context, get_current_user
from gosmart_graphite.main import app
from gosmart_graphite.service.system_service import system_service


@pytest.fixture
def client(isolated_app_lifespan):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "usr_test_sys_admin",
        "primary_email": "admin@gosmart.health",
        "system_roles": ["PLATFORM_ADMIN"],
        "is_active": True,
    }
    # The system router also applies the router-level get_current_group_context dependency;
    # override it so group-affiliation resolution never queries the external Neo4j database.
    app.dependency_overrides[get_current_group_context] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_system_bootstrap_and_get(client):
    """Test POST /api/v1/system/bootstrap and GET /api/v1/system endpoints."""
    mock_system = {
        "id": "sys_mock_id",
        "name": "GoSmart Health VNA",
        "owner": "GoSmart Health",
        "address": "Half Moon Bay, California",
        "email": "admin@gosmart.health",
        "phone": "+1 (650) 535-0097",
        "serial_number": "GSH-VNA-0001",
        "installed_at": "2026-09-30T00:00:00Z",
        "test_id": None,
        "action": "CREATE",
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": None,
        "configurations_count": 1,
        "configurations": [
            {
                "id": "cfg_mock_id",
                "ae_title": "*",
                "roles": ["SCP"],
                "host": "0.0.0.0",
                "port": 11112,
                "is_promiscuous": True,
                "tls_enabled": False,
                "tls_cert_path": None,
                "tls_key_path": None,
                "tls_ca_path": None,
                "tls_ciphers": None,
                "dicom_services": ["*"],
                "transfer_syntaxes": ["*"],
                "dicomweb_enabled": True,
                "dicomweb_url_prefix": "/dicomweb",
                "dicomweb_qido_enabled": True,
                "dicomweb_wado_enabled": True,
                "dicomweb_stow_enabled": True,
                "description": "Default Promiscuous DICOM SCP",
                "test_id": None,
                "action": "CREATE",
                "created_at": "2026-09-30T00:00:00Z",
                "updated_at": None,
            }
        ],
    }

    mock_prom = mock_system["configurations"][0]

    with patch.object(
        system_service,
        "bootstrap_system",
        new=AsyncMock(
            return_value={
                "success": True,
                "message": "Bootstrapped",
                "system": mock_system,
                "promiscuous_config": mock_prom,
            }
        ),
    ):
        res_boot = client.post("/api/v1/system/bootstrap")
        assert res_boot.status_code == 200
        boot_data = res_boot.json()
        assert boot_data["success"] is True
        assert boot_data["system"]["name"] == "GoSmart Health VNA"
        assert boot_data["promiscuous_config"]["ae_title"] == "*"

    with patch.object(system_service, "get_system", new=AsyncMock(return_value=mock_system)):
        res_get = client.get("/api/v1/system")
        assert res_get.status_code == 200
        get_data = res_get.json()
        assert get_data["id"] == "sys_mock_id"
        assert get_data["owner"] == "GoSmart Health"
        assert len(get_data["configurations"]) == 1


def test_api_system_update(client):
    """Test PUT /api/v1/system endpoint."""
    updated_sys = {
        "id": "sys_mock_id",
        "name": "Updated Clinic VNA",
        "owner": "Bay Area Clinic",
        "address": "789 Mission St",
        "email": "contact@bayclinic.org",
        "phone": "+1 415 555 1234",
        "serial_number": "BAC-001",
        "installed_at": "2026-09-30T00:00:00Z",
        "test_id": None,
        "action": "UPDATE",
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": "2026-09-30T01:00:00Z",
        "configurations_count": 0,
        "configurations": [],
    }

    with patch.object(system_service, "update_system", new=AsyncMock(return_value=updated_sys)):
        res = client.put(
            "/api/v1/system",
            json={
                "name": "Updated Clinic VNA",
                "owner": "Bay Area Clinic",
                "email": "contact@bayclinic.org",
            },
            headers={"X-User-ID": "lead_installer"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "Updated Clinic VNA"
        assert data["owner"] == "Bay Area Clinic"


def test_api_dicom_configuration_crud(client):
    """Test CRUD endpoints for /api/v1/configurations/dicom."""
    mock_config = {
        "id": "cfg_cardio_1",
        "ae_title": "CARDIO_ECHO",
        "roles": ["SCP", "SCU"],
        "host": "192.168.10.15",
        "port": 11115,
        "is_promiscuous": False,
        "tls_enabled": False,
        "tls_cert_path": None,
        "tls_key_path": None,
        "tls_ca_path": None,
        "tls_ciphers": None,
        "dicom_services": ["Verification", "Storage"],
        "transfer_syntaxes": ["*"],
        "dicomweb_enabled": True,
        "dicomweb_url_prefix": "/dicomweb",
        "dicomweb_qido_enabled": True,
        "dicomweb_wado_enabled": True,
        "dicomweb_stow_enabled": True,
        "description": "Cardiology Echo Node",
        "test_id": None,
        "action": "CREATE",
        "created_at": "2026-09-30T00:00:00Z",
        "updated_at": None,
    }

    # 1. Create
    with patch.object(system_service, "create_configuration", new=AsyncMock(return_value=mock_config)):
        res_create = client.post(
            "/api/v1/configurations/dicom",
            json={
                "ae_title": "CARDIO_ECHO",
                "roles": ["SCP", "SCU"],
                "host": "192.168.10.15",
                "port": 11115,
                "description": "Cardiology Echo Node",
            },
        )
        assert res_create.status_code == 201
        assert res_create.json()["ae_title"] == "CARDIO_ECHO"

    # 2. List
    with patch.object(system_service, "list_configurations", new=AsyncMock(return_value=[mock_config])):
        res_list = client.get("/api/v1/configurations/dicom")
        assert res_list.status_code == 200
        configs = res_list.json()
        assert len(configs) == 1
        assert configs[0]["id"] == "cfg_cardio_1"

    # 3. Get single
    with patch.object(system_service, "get_configuration", new=AsyncMock(return_value=mock_config)):
        res_get = client.get("/api/v1/configurations/dicom/cfg_cardio_1")
        assert res_get.status_code == 200
        assert res_get.json()["id"] == "cfg_cardio_1"

    # 4. Update
    updated_config = dict(mock_config)
    updated_config["description"] = "Updated Description"
    with patch.object(system_service, "update_configuration", new=AsyncMock(return_value=updated_config)):
        res_put = client.put(
            "/api/v1/configurations/dicom/cfg_cardio_1",
            json={"description": "Updated Description"},
        )
        assert res_put.status_code == 200
        assert res_put.json()["description"] == "Updated Description"

    # 5. Delete (soft)
    with patch.object(system_service, "delete_configuration", new=AsyncMock(return_value=True)):
        res_del = client.delete("/api/v1/configurations/dicom/cfg_cardio_1")
        assert res_del.status_code == 200
        assert res_del.json()["success"] is True

    # 6. Restore
    restored_config = dict(mock_config)
    restored_config["action"] = "RESTORE"
    with patch.object(system_service, "restore_configuration", new=AsyncMock(return_value=restored_config)):
        res_restore = client.post("/api/v1/configurations/dicom/cfg_cardio_1/restore")
        assert res_restore.status_code == 200
        assert res_restore.json()["action"] == "RESTORE"


def test_api_system_test_cleanup(client):
    """Test POST /api/v1/system/test/cleanup endpoint."""
    mock_res = {
        "success": True,
        "message": "Test data cleanup completed successfully. Deleted 6 test nodes.",
        "total_deleted": 6,
        "deleted_by_label": {
            "Patient": 1,
            "Study": 1,
            "Series": 1,
            "Instance": 1,
            "DICOMConfiguration": 1,
            "System": 1,
        },
        "labels_cleaned": ["System", "DICOMConfiguration", "Instance", "Patient", "Series", "Study"],
        "test_id": "test_mock_123",
    }
    with patch.object(system_service, "cleanup_all_test_data", new=AsyncMock(return_value=mock_res)):
        res = client.post("/api/v1/system/test/cleanup?test_id=test_mock_123")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["total_deleted"] == 6
        assert data["deleted_by_label"]["Patient"] == 1
        assert data["test_id"] == "test_mock_123"
        assert len(data["labels_cleaned"]) == 6


@pytest.mark.anyio
async def test_fhir_config_service_group_resolution_and_hierarchy():
    """Verify FHIRConfigService resolves groups by ID, name, or code and supports upward hierarchy resolution."""
    from unittest.mock import MagicMock

    from gosmart_graphite.service.fhir_config_service import FHIRConfigService

    mock_client = MagicMock()
    svc = FHIRConfigService(client=mock_client)

    # 1. Resolve by name ("GoSmart Health")
    mock_client.execute_query = AsyncMock(
        return_value=[{"group": {"id": "grp_root", "name": "GoSmart Health", "code": "GOSMART_HEALTH"}}]
    )
    resolved = await svc.resolve_target_group("GoSmart Health")
    assert resolved["id"] == "grp_root"
    query_sent = mock_client.execute_query.call_args.args[0]
    assert "g.name = $group_id" in query_sent
    assert "toUpper(g.code) = toUpper($group_id)" in query_sent

    # 2. Ambiguous group identifier raises RuntimeError
    mock_client.execute_query = AsyncMock(
        return_value=[
            {"group": {"id": "grp_1", "name": "Clinic", "code": "C1"}},
            {"group": {"id": "grp_2", "name": "Clinic", "code": "C2"}},
        ]
    )
    with pytest.raises(RuntimeError, match="Ambiguous group match"):
        await svc.resolve_target_group("Clinic")

    # 3. Upward hierarchy resolution picks nearest ancestor (depth 0 over depth 2)
    mock_client.execute_query = AsyncMock(
        return_value=[
            {
                "config": {
                    "id": "fhir_child",
                    "url": "https://child.fhir/r4",
                    "accession_system": "urn:oid:1.2.3.4",
                    "patient_id_system": "urn:oid:2.16.840.1.113883.4.3.25",
                    "poll_sec": 60,
                },
                "group_id": "grp_child",
                "depth": 0,
            },
            {
                "config": {
                    "id": "fhir_root",
                    "url": "https://root.fhir/r4",
                    "accession_system": "",
                    "patient_id_system": "",
                    "poll_sec": 120,
                },
                "group_id": "grp_root",
                "depth": 2,
            },
        ]
    )
    nearest = await svc.resolve_config_for_group(group_id="grp_child")
    assert nearest is not None
    assert nearest.id == "fhir_child"
    assert nearest.url == "https://child.fhir/r4"


@pytest.mark.anyio
async def test_system_service_create_configuration_attaches_to_group():
    """Verify SystemService.create_configuration resolves the target group and attaches to (:Group), not (:System)."""
    from gosmart_graphite.model.dicom_config import DICOMConfigurationCreateRequest
    from gosmart_graphite.service.group_service import group_service
    from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

    req = DICOMConfigurationCreateRequest(
        ae_title="MDICOM",
        roles=["SCP", "SCU"],
        host="0.0.0.0",
        port=11112,
        is_local=True,
        storage_root_uri="file://~/.gosmart/vna",
        description="GoSmart Local DICOM SCP",
    )

    mock_group = {"id": "grp_root", "name": "GoSmart Health", "code": "GOSMART_HEALTH", "group_type": "ORGANIZATION"}
    captured_queries: list[tuple[str, dict]] = []
    saved_cfg = {
        "id": "cfg_mdicom",
        "ae_title": "MDICOM",
        "roles": ["SCP", "SCU"],
        "host": "0.0.0.0",
        "port": 11112,
        "is_local": True,
        "is_promiscuous": False,
        "tls_enabled": False,
        "dicom_services": ["*"],
        "transfer_syntaxes": ["*"],
        "storage_root_uri": "file://~/.gosmart/vna",
        "dicomweb_enabled": True,
        "dicomweb_url_prefix": "/dicomweb",
        "dicomweb_qido_enabled": True,
        "dicomweb_wado_enabled": True,
        "dicomweb_stow_enabled": True,
        "description": "GoSmart Local DICOM SCP",
        "Action": "CREATE",
        "CreatedAt": "2026-11-18T00:00:00Z",
    }

    async def _fake_execute_query(query: str, parameters: dict):
        captured_queries.append((query, parameters))
        if "MERGE (c:DICOMConfiguration" in query:
            return [{"c": dict(saved_cfg)}]
        return [{"rel": {"id": "rel_serves"}, "config": dict(saved_cfg), "group": mock_group}]

    with (
        patch.object(group_service, "resolve_target_group", new=AsyncMock(return_value=mock_group)) as mock_resolve,
        patch("gosmart_graphite.db.neo4j.neo4j_client.execute_query", side_effect=_fake_execute_query),
        patch("gosmart_graphite.service.group_service.record_audit"),
        patch.object(pynetdicom_service, "on_configuration_added"),
    ):
        res = await system_service.create_configuration(
            config_data=req,
            acted_by="cli_admin",
            assign_group="GoSmart Health",
        )

    assert mock_resolve.await_args_list[0].kwargs == {"identifier": "GoSmart Health", "test_id": None}
    assert mock_resolve.await_args_list[1].kwargs == {"identifier": "grp_root", "test_id": None}
    assert len(captured_queries) == 2

    cfg_cypher, cfg_params = captured_queries[0]
    assert "MERGE (c:DICOMConfiguration {id: $cfg.id})" in cfg_cypher
    assert "OPTIONAL MATCH (:System)-[stale_sys_rel:HAS_DICOM_CONFIGURATION]->(c)" in cfg_cypher
    assert cfg_params["cfg"]["storage_root_uri"] == "file://~/.gosmart/vna"

    assoc_cypher, assoc_params = captured_queries[1]
    assert "MATCH (g:Group {id: $group_id})" in assoc_cypher
    assert "MERGE (g)-[r_has:HAS_DICOM_CONFIGURATION {id: $has_rel_id}]->(c)" in assoc_cypher
    assert "MERGE (c)-[r:SERVES {id: $rel_id}]->(g)" in assoc_cypher
    assert assoc_params["group_id"] == "grp_root"

    assert res["group_id"] == "grp_root"
    assert res["group_ids"] == ["grp_root"]
    assert res["storage_root_uri"] == "file://~/.gosmart/vna"


def test_group_service_upward_relations_include_has_dicom_configuration():
    """Verify UPWARD_GROUP_RELATION_TYPES includes HAS_DICOM_CONFIGURATION for my_group upward traversal."""
    from gosmart_graphite.service.group_service import UPWARD_GROUP_RELATION_TYPES

    assert "HAS_DICOM_CONFIGURATION" in UPWARD_GROUP_RELATION_TYPES
    assert "SERVES" in UPWARD_GROUP_RELATION_TYPES


def test_cli_configure_dicom_and_fhir_group_assignment(tmp_path):
    """Verify CLI `configure dicom-config` and `configure fhir-config` pass `--assign` to the underlying services."""
    import json

    from typer.testing import CliRunner

    from gosmart_graphite.cli import cli_app
    from gosmart_graphite.db.neo4j import neo4j_client
    from gosmart_graphite.service.fhir_config_service import fhir_config_service

    runner = CliRunner()

    # 1. configure dicom-config --assign "GoSmart Health"
    dicom_file = tmp_path / "dicom_cfg.json"
    dicom_file.write_text(
        json.dumps(
            {
                "ae_title": "MDICOM",
                "roles": ["SCP", "SCU"],
                "host": "0.0.0.0",
                "port": 11112,
                "is_local": True,
                "storage_root_uri": "file://~/.gosmart/vna",
            }
        ),
        encoding="utf-8",
    )
    mock_dicom_saved = {
        "id": "cfg_mdicom",
        "ae_title": "MDICOM",
        "roles": ["SCP", "SCU"],
        "host": "0.0.0.0",
        "port": 11112,
        "is_local": True,
        "is_promiscuous": False,
        "tls_enabled": False,
        "dicom_services": ["*"],
        "transfer_syntaxes": ["*"],
        "storage_root_uri": "file://~/.gosmart/vna",
        "dicomweb_enabled": True,
        "dicomweb_url_prefix": "/dicomweb",
        "group_id": "grp_root",
        "group_ids": ["grp_root"],
        "groups": [
            {"id": "grp_root", "name": "GoSmart Health", "code": "GOSMART_HEALTH", "group_type": "ORGANIZATION"}
        ],
        "action": "CREATE",
    }
    with (
        patch("gosmart_graphite.cli.configure._ensure_cli_db_ready", new=AsyncMock()),
        patch.object(
            system_service,
            "create_configuration",
            new=AsyncMock(return_value=mock_dicom_saved),
        ) as mock_create_dicom,
        patch.object(neo4j_client, "close", new=AsyncMock()),
    ):
        res_dicom = runner.invoke(
            cli_app,
            ["configure", "dicom-config", "--file", str(dicom_file), "--assign", "GoSmart Health"],
        )
        assert res_dicom.exit_code == 0, res_dicom.output
        assert "MDICOM" in res_dicom.output
        assert "GoSmart Health" in res_dicom.output
        mock_create_dicom.assert_awaited_once()
        assert mock_create_dicom.call_args.kwargs["assign_group"] == "GoSmart Health"

    # 2. configure fhir-config --assign "GoSmart Health"
    fhir_file = tmp_path / "fhir_cfg.json"
    fhir_file.write_text(
        json.dumps(
            {
                "url": "http://localhost:8103/fhir/R4",
                "accession_system": "urn:oid:1.2.3.4",
                "patient_id_system": "urn:oid:2.16.840.1.113883.4.3.25",
                "poll_sec": 60,
            }
        ),
        encoding="utf-8",
    )
    mock_fhir_saved = {
        "id": "fhir_medplum",
        "url": "http://localhost:8103/fhir/R4",
        "accession_system": "urn:oid:1.2.3.4",
        "patient_id_system": "urn:oid:2.16.840.1.113883.4.3.25",
        "poll_sec": 60,
        "group_ids": ["grp_root"],
        "groups": [{"id": "grp_root", "name": "GoSmart Health", "code": "GOSMART_HEALTH"}],
        "action": "CREATE",
    }
    with (
        patch("gosmart_graphite.cli.configure._ensure_cli_db_ready", new=AsyncMock()),
        patch.object(
            fhir_config_service,
            "create_fhir_config",
            new=AsyncMock(return_value=mock_fhir_saved),
        ) as mock_create_fhir,
        patch.object(neo4j_client, "close", new=AsyncMock()),
    ):
        res_fhir = runner.invoke(
            cli_app,
            ["configure", "fhir-config", "--file", str(fhir_file), "--assign", "GoSmart Health"],
        )
        assert res_fhir.exit_code == 0, res_fhir.output
        assert "http://localhost:8103/fhir/R4" in res_fhir.output
        assert "GoSmart Health" in res_fhir.output
        mock_create_fhir.assert_awaited_once()
        assert mock_create_fhir.call_args.kwargs["assign_group_id"] == "GoSmart Health"
