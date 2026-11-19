import io

import pytest
from fastapi.testclient import TestClient

from gosmart_graphite.core.auth_dependency import get_current_user
from gosmart_graphite.main import app
from gosmart_graphite.service.dicom_service import dicom_service


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "usr_test_dicomweb_admin",
        "primary_email": "dicomweb@gosmart.health",
        "system_roles": ["PLATFORM_ADMIN"],
        "is_active": True,
    }
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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


def test_dicomweb_stow_rs_provenance(client):
    """Verify STOW-RS captures client IP, user ID scaffolding, and service identity."""
    from unittest.mock import MagicMock, patch

    from gosmart_graphite.model.actor_message import DicomIngestionMessage

    ds = dicom_service.create_mock_dicom(
        patient_id="STOW-PROV-001",
        modality="MR",
    )
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    dicom_bytes = buffer.getvalue()

    mock_actor = MagicMock()
    mock_actor.is_alive.return_value = True

    with patch("gosmart_graphite.api.v1.endpoints.dicomweb.get_dicom_actor", return_value=mock_actor):
        response = client.post(
            "/dicomweb/studies",
            content=dicom_bytes,
            headers={
                "Content-Type": "application/dicom",
                "X-User-ID": "dr_watson",
            },
        )
        assert response.status_code == 200
        mock_actor.tell.assert_called_once()
        msg = mock_actor.tell.call_args[0][0]
        assert isinstance(msg, DicomIngestionMessage)
        assert msg.type == "INGEST_FILE"
        assert msg.source.startswith("stow:")
        assert msg.source_identity == "user:dr_watson"
        assert msg.destination.startswith("stow:")
        assert msg.destination_identity == "service:dicomweb"
        assert msg.acted_by == "user:dr_watson"


def test_dicomweb_qido_series_and_instances(client):
    """Verify QIDO-RS series and instance level queries."""
    ds = dicom_service.create_mock_dicom(
        patient_id="QIDO-SERIES-001",
        modality="CT",
        study_description="QIDO Test Study",
    )
    study_uid = ds.StudyInstanceUID
    series_uid = ds.SeriesInstanceUID
    sop_uid = ds.SOPInstanceUID
    dicom_service.save_raw_dicom(ds, sop_uid)

    # Ingest into graph synchronously if Neo4j is available
    hierarchy = dicom_service.parse_dataset(ds)
    hierarchy.test_id = "test_qido_series"
    from gosmart_graphite.service.graph_service import graph_service

    try:
        graph_service.save_hierarchy_sync(hierarchy)
    except Exception:
        pass

    # 1. Query series
    res_series = client.get(f"/dicomweb/studies/{study_uid}/series")
    assert res_series.status_code == 200
    assert res_series.headers["content-type"] == "application/dicom+json"
    series_list = res_series.json()
    assert isinstance(series_list, list)

    # 2. Query instances
    res_inst = client.get(f"/dicomweb/studies/{study_uid}/series/{series_uid}/instances")
    assert res_inst.status_code == 200
    assert res_inst.headers["content-type"] == "application/dicom+json"
    inst_list = res_inst.json()
    assert isinstance(inst_list, list)


def test_dicomweb_wado_series_and_study_bulk(client):
    """Verify WADO-RS study and series bulk retrieval returns multipart/related."""
    ds1 = dicom_service.create_mock_dicom(
        patient_id="BULK-TEST-001",
        modality="MR",
    )
    study_uid = ds1.StudyInstanceUID
    series_uid = ds1.SeriesInstanceUID
    dicom_service.save_raw_dicom(ds1, ds1.SOPInstanceUID)

    # Study bulk retrieval
    res_study = client.get(f"/dicomweb/studies/{study_uid}")
    assert res_study.status_code == 200
    assert "multipart/related" in res_study.headers["content-type"]
    assert b"Content-Type: application/dicom" in res_study.content

    # Series bulk retrieval
    res_series = client.get(f"/dicomweb/studies/{study_uid}/series/{series_uid}")
    assert res_series.status_code == 200
    assert "multipart/related" in res_series.headers["content-type"]
    assert b"Content-Type: application/dicom" in res_series.content


def test_dicomweb_stow_rs_multipart(client):
    """Verify STOW-RS supports multipart/related payloads with multiple instances."""
    ds1 = dicom_service.create_mock_dicom(
        patient_id="MULTI-STOW-001",
        modality="CT",
    )
    ds2 = dicom_service.create_mock_dicom(
        patient_id="MULTI-STOW-001",
        modality="CT",
    )

    buf1 = io.BytesIO()
    ds1.save_as(buf1, enforce_file_format=True)
    buf2 = io.BytesIO()
    ds2.save_as(buf2, enforce_file_format=True)

    boundary = "TEST_MULTIPART_BOUNDARY"
    body = (
        f"--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + buf1.getvalue()
        + f"\r\n--{boundary}\r\nContent-Type: application/dicom\r\n\r\n".encode()
        + buf2.getvalue()
        + f"\r\n--{boundary}--\r\n".encode()
    )

    response = client.post(
        "/dicomweb/studies",
        content=body,
        headers={"Content-Type": f'multipart/related; type="application/dicom"; boundary={boundary}'},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/dicom+json"
    data = response.json()
    assert "00081199" in data
    referenced = data["00081199"]["Value"]
    assert len(referenced) == 2
    uids = [item["00081155"]["Value"][0] for item in referenced]
    assert ds1.SOPInstanceUID in uids
    assert ds2.SOPInstanceUID in uids


def test_dicomweb_graph_qido_live_traversal(client):
    """Verify QIDO-RS reads directly from Neo4j Knowledge Graph with live traversal counts."""
    from pydicom.uid import generate_uid

    from gosmart_graphite.db.neo4j import neo4j_client
    from gosmart_graphite.service.graph_service import graph_service

    try:
        neo4j_client.get_sync_driver().verify_connectivity()
    except Exception:
        pytest.skip("Neo4j database is not reachable")

    test_study_uid = generate_uid()
    test_patient_id = "PT-QIDO-GRAPH-01"

    # Create 2 slices in Series 1 (MR) and 1 slice in Series 2 (CT) under the same study
    ds_mr1 = dicom_service.create_mock_dicom(
        patient_id=test_patient_id,
        patient_name="TEST^GRAPH^QIDO",
        modality="MR",
        study_description="Brain Multi-Sequence",
    )
    ds_mr1.StudyInstanceUID = test_study_uid

    ds_mr2 = dicom_service.create_mock_dicom(
        patient_id=test_patient_id,
        patient_name="TEST^GRAPH^QIDO",
        modality="MR",
        study_description="Brain Multi-Sequence",
    )
    ds_mr2.StudyInstanceUID = test_study_uid
    ds_mr2.SeriesInstanceUID = ds_mr1.SeriesInstanceUID
    ds_mr2.SeriesNumber = 1

    ds_ct = dicom_service.create_mock_dicom(
        patient_id=test_patient_id,
        patient_name="TEST^GRAPH^QIDO",
        modality="CT",
        study_description="Brain Multi-Sequence",
    )
    ds_ct.StudyInstanceUID = test_study_uid
    ds_ct.SeriesNumber = 2

    # Ingest into Neo4j
    for ds in (ds_mr1, ds_mr2, ds_ct):
        h = dicom_service.parse_dataset(ds)
        h.test_id = "test_qido_graph_live"
        graph_service.save_hierarchy_sync(h)

    # Execute QIDO query via HTTP endpoint
    response = client.get(f"/dicomweb/studies?StudyInstanceUID={test_study_uid}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/dicom+json"
    studies = response.json()
    assert len(studies) == 1
    study_json = studies[0]

    # Verify Patient & Study Properties read directly from graph
    assert study_json["00100020"]["Value"][0] == test_patient_id
    assert study_json["00100010"]["Value"][0]["Alphabetic"] == "TEST^GRAPH^QIDO"
    assert study_json["00081030"]["Value"][0] == "Brain Multi-Sequence"
    assert study_json["0020000D"]["Value"][0] == test_study_uid

    # Verify traversal-computed live counts: 2 series, 3 instances
    assert study_json["00201206"]["Value"][0] == 2  # NumberOfStudyRelatedSeries
    assert study_json["00201208"]["Value"][0] == 3  # NumberOfStudyRelatedInstances

    # Verify collected modalities: both 'MR' and 'CT'
    modalities = study_json["00080061"]["Value"]
    assert "MR" in modalities
    assert "CT" in modalities
