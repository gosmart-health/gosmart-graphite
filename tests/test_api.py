import pytest
from fastapi.testclient import TestClient

from gosmart_graphite.main import app
from gosmart_graphite.service.dicom_service import dicom_service


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert data["storage_root"].startswith("file://")
    assert data["db_connection"].startswith("bolt://") or data["db_connection"].startswith("neo4j")


def test_health_endpoint(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "services" in data
    assert "pykka_actor" in data["services"]
    assert "neo4j" in data["services"]


def test_dicom_parsing():
    ds = dicom_service.create_mock_dicom(
        patient_id="UNIT-TEST-001",
        patient_name="TEST^PATIENT",
        modality="CT",
        study_description="Thoracic CT",
    )
    hierarchy = dicom_service.parse_dataset(ds)
    assert hierarchy.patient.patient_id == "UNIT-TEST-001"
    assert hierarchy.series.modality == "CT"
    assert hierarchy.study.study_description == "Thoracic CT"
    assert hierarchy.instance.sop_instance_uid is not None
