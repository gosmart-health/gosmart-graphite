import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from gosmart_graphite.actor.audit_actor import get_audit_actor, stop_audit_actor
from gosmart_graphite.actor.dicom_ingestion_actor import stop_dicom_actor
from gosmart_graphite.core.auth_dependency import get_current_user
from gosmart_graphite.main import app
from gosmart_graphite.model.audit import AuditAction
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.dicomweb_service import dicomweb_service
from gosmart_graphite.service.graph_service import graph_service


@pytest.fixture(scope="module", autouse=True)
def teardown_module_actors():
    """Ensure all background actor threads are stopped after test module execution."""
    yield
    stop_audit_actor()
    stop_dicom_actor()


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


def _clear_test_data(test_id: str):
    """Pre-test cleanup for deterministic test isolation."""
    graph_service.cleanup_test_data_sync(test_id)


def test_dicomweb_service_qido_studies_audit():
    """Verify DicomWebService.search_studies logs audit entry to Neo4j."""
    test_id = f"test_qido_studies_{uuid.uuid4().hex[:8]}"
    _clear_test_data(test_id)

    get_audit_actor()
    results = dicomweb_service.search_studies(
        patient_id="PAT_AUDIT_01",
        study_uid="1.2.826.0.1.3680043.8.498.11111",
        test_id=test_id,
        actor_id="user:radiologist_01",
        actor="radiologist@hospital.org",
    )
    assert isinstance(results, list)

    # Allow actor thread to process
    time.sleep(0.4)

    logs = graph_service.search_audit_logs_sync(test_id=test_id)
    assert len(logs) >= 1
    log = logs[0]
    assert log["action"] == AuditAction.DICOMWEB_STUDIES_QUERIED.value
    assert log["statusCode"] == 200
    assert log["severity"] == "INFO"
    assert log["actorId"] == "user:radiologist_01"
    assert log["actor"] == "radiologist@hospital.org"
    assert "PAT_AUDIT_01" in str(log.get("details", ""))


def test_dicomweb_service_qido_series_and_instances_audit():
    """Verify DicomWebService search_series and search_instances log audit entries."""
    test_id = f"test_qido_series_inst_{uuid.uuid4().hex[:8]}"
    _clear_test_data(test_id)

    get_audit_actor()
    dicomweb_service.search_series(
        study_uid="1.2.826.0.1.3680043.8.498.22222",
        modality="CT",
        test_id=test_id,
        actor_id="service:dicomweb",
        actor="dicomweb",
    )
    dicomweb_service.search_instances(
        study_uid="1.2.826.0.1.3680043.8.498.22222",
        series_uid="1.2.826.0.1.3680043.8.498.33333",
        test_id=test_id,
        actor_id="service:dicomweb",
        actor="dicomweb",
    )

    time.sleep(0.4)

    logs = graph_service.search_audit_logs_sync(test_id=test_id)
    actions = [entry["action"] for entry in logs]
    assert AuditAction.DICOMWEB_SERIES_QUERIED.value in actions
    assert AuditAction.DICOMWEB_INSTANCES_QUERIED.value in actions


def test_dicomweb_service_wado_instance_audit():
    """Verify DicomWebService.get_instance_file logs audit for found and not found instances."""
    test_id = f"test_wado_instance_{uuid.uuid4().hex[:8]}"
    _clear_test_data(test_id)

    get_audit_actor()

    # 1. Create and save a valid test DICOM file
    ds = dicom_service.create_mock_dicom(
        patient_id="WADO-AUDIT-001",
        modality="MR",
    )
    sop_uid = ds.SOPInstanceUID
    study_uid = ds.StudyInstanceUID
    series_uid = ds.SeriesInstanceUID
    dicom_service.save_raw_dicom(ds, sop_uid)

    # 2. Retrieve existing instance (Expect 200 Audit)
    file_path = dicomweb_service.get_instance_file(
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=sop_uid,
        actor_id="user:technician_01",
        actor="tech@hospital.org",
        test_id=test_id,
    )
    assert file_path is not None
    assert file_path.exists()

    # 3. Retrieve non-existent instance (Expect 404 Audit)
    missing_sop_uid = f"1.2.826.0.1.3680043.8.498.missing.{uuid.uuid4().hex[:6]}"
    missing_path = dicomweb_service.get_instance_file(
        study_uid=study_uid,
        series_uid=series_uid,
        sop_uid=missing_sop_uid,
        actor_id="user:technician_01",
        actor="tech@hospital.org",
        test_id=test_id,
    )
    assert missing_path is None

    time.sleep(0.4)

    logs = graph_service.search_audit_logs_sync(test_id=test_id)
    assert len(logs) == 2

    success_logs = [entry for entry in logs if entry["statusCode"] == 200]
    not_found_logs = [entry for entry in logs if entry["statusCode"] == 404]

    assert len(success_logs) == 1
    assert success_logs[0]["action"] == AuditAction.DICOMWEB_INSTANCE_RETRIEVED.value
    assert success_logs[0]["severity"] == "INFO"
    assert success_logs[0]["target"] == sop_uid

    assert len(not_found_logs) == 1
    assert not_found_logs[0]["action"] == AuditAction.DICOMWEB_INSTANCE_RETRIEVED.value
    assert not_found_logs[0]["severity"] == "WARNING"
    assert not_found_logs[0]["target"] == missing_sop_uid


def test_dicomweb_service_wado_series_and_study_audit():
    """Verify DicomWebService get_series_files and get_study_files log audit entries."""
    test_id = f"test_wado_bulk_{uuid.uuid4().hex[:8]}"
    _clear_test_data(test_id)

    get_audit_actor()

    dicomweb_service.get_series_files(
        study_uid="1.2.826.0.1.3680043.8.498.study.bulk",
        series_uid="1.2.826.0.1.3680043.8.498.series.bulk",
        test_id=test_id,
    )
    dicomweb_service.get_study_files(
        study_uid="1.2.826.0.1.3680043.8.498.study.bulk",
        test_id=test_id,
    )

    time.sleep(0.4)

    logs = graph_service.search_audit_logs_sync(test_id=test_id)
    actions = [entry["action"] for entry in logs]
    assert AuditAction.DICOMWEB_SERIES_RETRIEVED.value in actions
    assert AuditAction.DICOMWEB_STUDY_RETRIEVED.value in actions


def test_dicomweb_endpoints_record_audit_with_user_context(client):
    """Verify HTTP endpoints propagate user and test context to Neo4j audit logs."""
    test_id = f"test_http_endpoints_{uuid.uuid4().hex[:8]}"
    _clear_test_data(test_id)

    get_audit_actor()

    # 1. QIDO-RS Study Search
    headers = {"X-User-ID": "viewer_user", "X-Test-ID": test_id}
    res_qido = client.get("/dicomweb/studies?PatientID=AUDIT-HTTP-PAT", headers=headers)
    assert res_qido.status_code == 200

    # 2. STOW-RS Ingestion
    ds = dicom_service.create_mock_dicom(
        patient_id="STOW-AUDIT-PAT",
        modality="DX",
    )
    raw_buffer = io.BytesIO()
    ds.save_as(raw_buffer, write_like_original=False)
    dicom_bytes = raw_buffer.getvalue()

    res_stow = client.post(
        "/dicomweb/studies",
        content=dicom_bytes,
        headers={"Content-Type": "application/dicom", "X-User-ID": "stow_tech", "X-Test-ID": test_id},
    )
    assert res_stow.status_code == 200

    # 3. WADO-URI Legacy Retrieval
    res_wado_uri = client.get(
        f"/dicomweb/wado?requestType=WADO&studyUID={ds.StudyInstanceUID}&seriesUID={ds.SeriesInstanceUID}&objectUID={ds.SOPInstanceUID}",
        headers={"X-User-ID": "legacy_pacs", "X-Test-ID": test_id},
    )
    assert res_wado_uri.status_code == 200

    time.sleep(0.5)

    logs = graph_service.search_audit_logs_sync(test_id=test_id)
    actions = [entry["action"] for entry in logs]
    actors = [entry["actorId"] for entry in logs]

    assert AuditAction.DICOMWEB_STUDIES_QUERIED.value in actions
    assert AuditAction.DICOMWEB_INSTANCES_STORED.value in actions
    assert AuditAction.DICOMWEB_INSTANCE_RETRIEVED.value in actions

    assert "user:viewer_user" in actors
    assert "user:stow_tech" in actors
    assert "user:legacy_pacs" in actors
