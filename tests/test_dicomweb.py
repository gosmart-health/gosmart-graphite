import io

import pytest
from fastapi.testclient import TestClient

from main import app
from service.dicom_service import dicom_service


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_dicomweb_qido_studies(client):
    # Ensure at least one mock dataset exists in storage
    ds = dicom_service.create_mock_dicom(
        patient_id="QIDO-TEST-001",
        patient_name="TEST^QIDO",
        modality="CT",
    )
    dicom_service.save_raw_dicom(ds, ds.SOPInstanceUID)

    response = client.get("/dicomweb/studies?PatientID=QIDO-TEST-001")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/dicom+json"
    studies = response.json()
    assert isinstance(studies, list)
    assert len(studies) >= 1
    # Check standard DICOM JSON PatientID tag (0010,0020)
    assert "00100020" in studies[0]
    assert studies[0]["00100020"]["Value"][0] == "QIDO-TEST-001"


def test_dicomweb_wado_instance(client):
    ds = dicom_service.create_mock_dicom(
        patient_id="WADO-TEST-001",
        modality="MR",
    )
    sop_uid = ds.SOPInstanceUID
    study_uid = ds.StudyInstanceUID
    series_uid = ds.SeriesInstanceUID
    dicom_service.save_raw_dicom(ds, sop_uid)

    response = client.get(f"/dicomweb/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/dicom"
    assert len(response.content) > 0


def test_dicomweb_stow_rs(client):
    ds = dicom_service.create_mock_dicom(
        patient_id="STOW-TEST-001",
        modality="CT",
    )
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    dicom_bytes = buffer.getvalue()

    response = client.post(
        "/dicomweb/studies",
        content=dicom_bytes,
        headers={"Content-Type": "application/dicom"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/dicom+json"
    data = response.json()
    # Verify ReferencedSOPSequence (0008,1199)
    assert "00081199" in data
    assert len(data["00081199"]["Value"]) == 1
    assert data["00081199"]["Value"][0]["00081155"]["Value"][0] == ds.SOPInstanceUID
