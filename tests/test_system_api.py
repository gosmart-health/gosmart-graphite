from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app
from service.system_service import system_service


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


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
