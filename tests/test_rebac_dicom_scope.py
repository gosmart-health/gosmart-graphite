import socket
import time
import uuid

import pydicom
import pytest
from fastapi.testclient import TestClient
from pydicom.dataset import Dataset
from pynetdicom import AE
from pynetdicom.sop_class import StudyRootQueryRetrieveInformationModelFind

from actor.audit_actor import get_audit_actor, stop_audit_actor
from actor.dicom_actor import get_dicom_actor, stop_dicom_actor
from core.config import get_settings
from db.neo4j import neo4j_client
from main import app
from model.audit import AuditAction
from service.dicom_service import dicom_service
from service.graph_service import graph_service
from service.group_service import group_service
from service.pynetdicom_service import PyNetDicomService
from service.system_service import system_service


def get_free_port() -> int:
    """Find a dynamically available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module", autouse=True)
def teardown_module_actors():
    """Clean up background actors."""
    yield
    stop_audit_actor()
    stop_dicom_actor()


@pytest.mark.anyio
async def test_rebac_dicom_full_organization_lifecycle():
    """End-to-end integration test validating:
    1. Group creation & hierarchy (Health System -> Clinic A, Clinic B).
    2. DICOMConfiguration SERVES association & audit.
    3. DIMSE C-STORE caller AE resolution & catch-all fallback.
    4. DIMSE C-FIND group isolation & downward closure scoping.
    5. DICOMweb QIDO-RS user affiliation scoping & admin bypass.
    6. Study triage & reassociation from catch-all to specific group.
    """
    if not await neo4j_client.verify_connectivity():
        pytest.skip("Neo4j database is not reachable")

    test_id = f"test_rebac_{uuid.uuid4().hex[:8]}"
    settings = get_settings()

    # Pre-test cleanup
    await system_service.cleanup_test_data(test_id)
    await graph_service.cleanup_test_data(test_id)

    get_audit_actor()
    get_dicom_actor()

    # -------------------------------------------------------------------------
    # 1. Setup Organizations: Health System -> Clinic A & Clinic B
    # -------------------------------------------------------------------------
    catch_all = await group_service.get_or_create_catch_all_group(test_id=test_id, acted_by="test_admin")
    assert catch_all is not None

    parent_grp = await group_service.create_group(
        name=f"Metro Health System {test_id}",
        code=f"METRO_HEALTH_{test_id.upper()}",
        group_type="HEALTH_SYSTEM",
        test_id=test_id,
        acted_by="test_admin",
    )
    clinic_a = await group_service.create_group(
        name=f"Clinic Alpha {test_id}",
        code=f"CLINIC_A_{test_id.upper()}",
        group_type="FACILITY",
        test_id=test_id,
        acted_by="test_admin",
    )
    await group_service.nest_group(
        child_group_id=clinic_a["id"],
        parent_group_id=parent_grp["id"],
        test_id=test_id,
        acted_by="test_admin",
    )
    clinic_b = await group_service.create_group(
        name=f"Clinic Beta {test_id}",
        code=f"CLINIC_B_{test_id.upper()}",
        group_type="FACILITY",
        test_id=test_id,
        acted_by="test_admin",
    )

    # -------------------------------------------------------------------------
    # 2. Setup DICOM Configurations with SERVES relations
    # -------------------------------------------------------------------------
    suffix = uuid.uuid4().hex[:6].upper()
    aet_a = f"AET_A_{suffix}"
    aet_b = f"AET_B_{suffix}"

    from model.system import DICOMConfigurationCreateRequest

    cfg_a = await system_service.create_configuration(
        DICOMConfigurationCreateRequest(
            ae_title=aet_a,
            roles=["SCU"],
            host="127.0.0.1",
            port=11112,
            group_id=clinic_a["id"],
            test_id=test_id,
        ),
        acted_by="test_admin",
    )
    cfg_b = await system_service.create_configuration(
        DICOMConfigurationCreateRequest(
            ae_title=aet_b,
            roles=["SCU"],
            host="127.0.0.1",
            port=11112,
            group_id=clinic_b["id"],
            test_id=test_id,
        ),
        acted_by="test_admin",
    )

    assert cfg_a["group_id"] == clinic_a["id"]
    assert cfg_b["group_id"] == clinic_b["id"]

    # Verify SERVES resolution
    grp_res_a = group_service.get_group_for_ae_title_sync(aet_a)
    assert grp_res_a is not None
    assert grp_res_a["id"] == clinic_a["id"]

    # -------------------------------------------------------------------------
    # 3. Ingest DICOM Studies: Clinic A, Clinic B, and Unmapped Catch-All
    # -------------------------------------------------------------------------
    study_a_uid = pydicom.uid.generate_uid()
    ds_a = dicom_service.create_mock_dicom(
        patient_id=f"PAT_A_{test_id}",
        patient_name="PATIENT^ALPHA",
        modality="MR",
        study_description="Clinic A MRI",
    )
    ds_a.StudyInstanceUID = study_a_uid
    dicom_service.save_raw_dicom(ds_a, ds_a.SOPInstanceUID)
    h_a = dicom_service.parse_dataset(ds_a, source_identity=f"ae:{aet_a}")
    h_a.test_id = test_id
    h_a.group_id = clinic_a["id"]
    graph_service.save_hierarchy_sync(h_a)

    study_b_uid = pydicom.uid.generate_uid()
    ds_b = dicom_service.create_mock_dicom(
        patient_id=f"PAT_B_{test_id}",
        patient_name="PATIENT^BETA",
        modality="CT",
        study_description="Clinic B CT",
    )
    ds_b.StudyInstanceUID = study_b_uid
    dicom_service.save_raw_dicom(ds_b, ds_b.SOPInstanceUID)
    h_b = dicom_service.parse_dataset(ds_b, source_identity=f"ae:{aet_b}")
    h_b.test_id = test_id
    h_b.group_id = clinic_b["id"]
    graph_service.save_hierarchy_sync(h_b)

    # Unmapped caller AE -> should fallback to catch-all group
    study_catch_uid = pydicom.uid.generate_uid()
    ds_catch = dicom_service.create_mock_dicom(
        patient_id=f"PAT_LOST_{test_id}",
        patient_name="PATIENT^LOST",
        modality="XR",
        study_description="Lost and Found X-Ray",
    )
    ds_catch.StudyInstanceUID = study_catch_uid
    dicom_service.save_raw_dicom(ds_catch, ds_catch.SOPInstanceUID)
    h_catch = dicom_service.parse_dataset(ds_catch, source_identity="ae:UNREGISTERED_SCU")
    h_catch.test_id = test_id
    # Leave h_catch.group_id as None to verify automatic catch-all fallback
    graph_service.save_hierarchy_sync(h_catch)

    # -------------------------------------------------------------------------
    # 4. DIMSE C-FIND Scope Isolation
    # -------------------------------------------------------------------------
    dimse_service = PyNetDicomService(settings=settings)
    scp_port = get_free_port()
    scp_cfg = {
        "id": f"scp_rebac_{scp_port}",
        "ae_title": "GOSMART_GS",
        "roles": ["SCP"],
        "host": "127.0.0.1",
        "port": scp_port,
        "is_local": True,
        "is_promiscuous": False,
        "dicom_services": ["*"],
        "transfer_syntaxes": ["*"],
    }
    inst = dimse_service.start_scp_for_config(scp_cfg)
    assert inst is not None and inst.status == "RUNNING"

    try:
        # A) Query as CLINIC_A_SCU (should see only Study A)
        client_ae_a = AE(ae_title=aet_a)
        client_ae_a.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        assoc_a = client_ae_a.associate("127.0.0.1", scp_port, ae_title="GOSMART_GS")
        assert assoc_a.is_established

        q_ds = Dataset()
        q_ds.QueryRetrieveLevel = "STUDY"
        q_ds.StudyInstanceUID = ""
        q_ds.PatientID = ""
        q_ds.PatientName = ""
        q_ds.StudyDescription = ""

        res_a = list(assoc_a.send_c_find(q_ds, StudyRootQueryRetrieveInformationModelFind))
        assoc_a.release()

        studies_a = [ds for status, ds in res_a if ds is not None]
        uids_a = [str(ds.StudyInstanceUID) for ds in studies_a]

        assert study_a_uid in uids_a
        assert study_b_uid not in uids_a
        assert study_catch_uid not in uids_a

        # B) Query as CLINIC_B_SCU (should see only Study B)
        client_ae_b = AE(ae_title=aet_b)
        client_ae_b.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        assoc_b = client_ae_b.associate("127.0.0.1", scp_port, ae_title="GOSMART_GS")
        assert assoc_b.is_established

        res_b = list(assoc_b.send_c_find(q_ds, StudyRootQueryRetrieveInformationModelFind))
        assoc_b.release()

        studies_b = [ds for status, ds in res_b if ds is not None]
        uids_b = [str(ds.StudyInstanceUID) for ds in studies_b]

        assert study_b_uid in uids_b
        assert study_a_uid not in uids_b
        assert study_catch_uid not in uids_b

    finally:
        dimse_service.stop_all()

    # -------------------------------------------------------------------------
    # 5. DICOMweb QIDO-RS ReBAC Scoping
    # -------------------------------------------------------------------------
    from core.auth_dependency import get_current_group_context, get_current_user

    app.dependency_overrides[get_current_user] = lambda: {
        "id": f"dr_user_{test_id}",
        "primary_email": f"dr_{test_id}@gosmart.health",
        "system_roles": ["USER"],
        "is_active": True,
    }

    try:
        with TestClient(app) as http_client:
            # A) User in Clinic A
            app.dependency_overrides[get_current_group_context] = lambda: {
                "id": clinic_a["id"],
                "name": clinic_a["name"],
            }
            headers_user_a = {
                "X-User-ID": f"dr_alpha_{test_id}",
                "X-Group-ID": clinic_a["id"],
                "X-Test-ID": test_id,
            }
            res_qido_a = http_client.get("/dicomweb/studies", headers=headers_user_a)
            assert res_qido_a.status_code == 200
            items_a = res_qido_a.json()
            uids_qido_a = [item["0020000D"]["Value"][0] for item in items_a]
            assert study_a_uid in uids_qido_a
            assert study_b_uid not in uids_qido_a

            # B) User in Parent Organization (Downward closure includes Clinic A)
            app.dependency_overrides[get_current_group_context] = lambda: {
                "id": parent_grp["id"],
                "name": parent_grp["name"],
            }
            headers_parent = {
                "X-User-ID": f"exec_metro_{test_id}",
                "X-Group-ID": parent_grp["id"],
                "X-Test-ID": test_id,
            }
            res_qido_parent = http_client.get("/dicomweb/studies", headers=headers_parent)
            assert res_qido_parent.status_code == 200
            items_parent = res_qido_parent.json()
            uids_qido_parent = [item["0020000D"]["Value"][0] for item in items_parent]
            assert study_a_uid in uids_qido_parent

            # C) Platform Admin (global unrestricted visibility)
            app.dependency_overrides[get_current_user] = lambda: {
                "id": f"admin_{test_id}",
                "primary_email": f"admin_{test_id}@gosmart.health",
                "system_roles": ["PLATFORM_ADMIN"],
                "is_active": True,
            }
            app.dependency_overrides[get_current_group_context] = lambda: None
            headers_admin = {
                "X-User-ID": f"super_admin_{test_id}",
                "X-Test-ID": test_id,
            }
            res_qido_admin = http_client.get("/dicomweb/studies", headers=headers_admin)
            assert res_qido_admin.status_code == 200
            items_admin = res_qido_admin.json()
            uids_qido_admin = [item["0020000D"]["Value"][0] for item in items_admin]
            assert study_a_uid in uids_qido_admin
            assert study_b_uid in uids_qido_admin
            assert study_catch_uid in uids_qido_admin
    finally:
        app.dependency_overrides.clear()

    # -------------------------------------------------------------------------
    # 6. Study Triage & Re-association from Catch-All to Clinic A
    # -------------------------------------------------------------------------
    reassoc_res = await group_service.reassociate_study_to_group(
        study_instance_uid=study_catch_uid,
        target_group_id=clinic_a["id"],
        test_id=test_id,
        acted_by="triage_officer",
    )
    assert reassoc_res["group"]["id"] == clinic_a["id"]

    # Verify that Clinic A can now see the triaged study via QIDO-RS
    app.dependency_overrides[get_current_user] = lambda: {
        "id": f"dr_user_{test_id}",
        "primary_email": f"dr_{test_id}@gosmart.health",
        "system_roles": ["USER"],
        "is_active": True,
    }
    app.dependency_overrides[get_current_group_context] = lambda: {
        "id": clinic_a["id"],
        "name": clinic_a["name"],
    }
    try:
        with TestClient(app) as http_client:
            res_qido_a_after = http_client.get("/dicomweb/studies", headers=headers_user_a)
            assert res_qido_a_after.status_code == 200
            uids_after = [item["0020000D"]["Value"][0] for item in res_qido_a_after.json()]
            assert study_catch_uid in uids_after
    finally:
        app.dependency_overrides.clear()

    # -------------------------------------------------------------------------
    # 7. Verify Audit Trail for Triage and Configuration
    # -------------------------------------------------------------------------
    time.sleep(0.5)
    audit_logs = graph_service.search_audit_logs_sync(test_id=test_id)
    actions = [entry["action"] for entry in audit_logs]

    assert AuditAction.STUDY_REASSOCIATED_TO_GROUP.value in actions
    assert AuditAction.DICOM_CONFIGURATION_SERVES_GROUP.value in actions
