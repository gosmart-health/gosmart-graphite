import uuid

import pytest
from fastapi.testclient import TestClient

from gosmart_graphite.core.security import create_access_token
from gosmart_graphite.main import app
from gosmart_graphite.service.dicom_service import dicom_service
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.group_service import group_service
from gosmart_graphite.service.user_service import user_service


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.anyio
async def test_hierarchical_rebac_and_cross_tenant_isolation(client):
    test_id = f"test_rebac_{uuid.uuid4().hex[:8]}"

    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        # 1. Construct 3-Tier Organizational Hierarchy
        # Level 1: Enterprise Health System
        enterprise_grp = await group_service.create_group(
            name="Bay Health Enterprise",
            code=f"BAY_ENT_{uuid.uuid4().hex[:4]}",
            group_type="HEALTH_SYSTEM",
            test_id=test_id,
        )

        # Level 2: Regional Hospital (Child of Enterprise)
        hospital_grp = await group_service.create_group(
            name="Peninsula Regional Hospital",
            code=f"PEN_HOSP_{uuid.uuid4().hex[:4]}",
            group_type="HOSPITAL",
            test_id=test_id,
        )
        await group_service.nest_group(
            child_group_id=hospital_grp["id"],
            parent_group_id=enterprise_grp["id"],
            test_id=test_id,
        )

        # Level 3A: Leaf Clinic A (Child of Hospital)
        clinic_a_grp = await group_service.create_group(
            name="Half Moon Bay Clinic",
            code=f"HMB_CLINIC_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )
        await group_service.nest_group(
            child_group_id=clinic_a_grp["id"],
            parent_group_id=hospital_grp["id"],
            test_id=test_id,
        )

        # Level 3B: Leaf Clinic B (Child of Hospital)
        clinic_b_grp = await group_service.create_group(
            name="San Mateo Specialty Center",
            code=f"SMSC_CLINIC_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )
        await group_service.nest_group(
            child_group_id=clinic_b_grp["id"],
            parent_group_id=hospital_grp["id"],
            test_id=test_id,
        )

        # 2. Provision Users with scoped affiliations
        # User 1: Enterprise Admin
        enterprise_admin = await user_service.create_local_user(
            username=f"ent_admin_{uuid.uuid4().hex[:6]}",
            email=f"admin_{uuid.uuid4().hex[:6]}@bayhealth.org",
            password="AdminPassword123!",
            display_name="Enterprise Chief Administrator",
            system_roles=["PLATFORM_ADMIN"],
            group_id=enterprise_grp["id"],
            group_roles=["Admin"],
            test_id=test_id,
        )

        # User 2: Hospital Supervisor
        hosp_supervisor = await user_service.create_local_user(
            username=f"hosp_sup_{uuid.uuid4().hex[:6]}",
            email=f"supervisor_{uuid.uuid4().hex[:6]}@peninsula.org",
            password="SupervisorPass123!",
            display_name="Hospital Operations Supervisor",
            system_roles=["USER"],
            group_id=hospital_grp["id"],
            group_roles=["Manager"],
            test_id=test_id,
        )

        # User 3: Clinic A Nurse
        clinic_a_nurse = await user_service.create_local_user(
            username=f"nurse_a_{uuid.uuid4().hex[:6]}",
            email=f"nurse_a_{uuid.uuid4().hex[:6]}@hmbclinic.org",
            password="NursePassword123!",
            display_name="Nurse Alice (HMB)",
            system_roles=["USER"],
            group_id=clinic_a_grp["id"],
            group_roles=["Viewer"],
            test_id=test_id,
        )

        # User 4: Clinic B Nurse
        clinic_b_nurse = await user_service.create_local_user(
            username=f"nurse_b_{uuid.uuid4().hex[:6]}",
            email=f"nurse_b_{uuid.uuid4().hex[:6]}@smscclinic.org",
            password="NursePassword123!",
            display_name="Nurse Bob (SMSC)",
            system_roles=["USER"],
            group_id=clinic_b_grp["id"],
            group_roles=["Viewer"],
            test_id=test_id,
        )

        # 3. Create mock DICOM Studies scoped to Clinic A and Clinic B
        # Study A at Clinic A
        study_a_uid = f"1.2.840.10008.{uuid.uuid4().int % 1000000}.1"
        ds_a = dicom_service.create_mock_dicom(
            patient_id=f"PAT-A-{uuid.uuid4().hex[:4]}",
            patient_name="PATIENT^CLINIC^A",
        )
        ds_a.StudyInstanceUID = study_a_uid
        hier_a = dicom_service.parse_dataset(ds_a)
        await graph_service.save_hierarchy(hier_a)

        # Link Study A to Clinic A Group
        link_query_a = """
        MATCH (s:Study {study_instance_uid: $study_uid})
        MATCH (g:Group {id: $group_id})
        MERGE (s)-[r:PERFORMED_AT {id: $rel_id}]->(g)
        ON CREATE SET r.test_id = $test_id
        """
        await graph_service.client.execute_query(
            link_query_a,
            {
                "study_uid": study_a_uid,
                "group_id": clinic_a_grp["id"],
                "rel_id": f"rel_perf_{uuid.uuid4().hex[:8]}",
                "test_id": test_id,
            },
        )

        # Study B at Clinic B
        study_b_uid = f"1.2.840.10008.{uuid.uuid4().int % 1000000}.2"
        ds_b = dicom_service.create_mock_dicom(
            patient_id=f"PAT-B-{uuid.uuid4().hex[:4]}",
            patient_name="PATIENT^CLINIC^B",
        )
        ds_b.StudyInstanceUID = study_b_uid
        hier_b = dicom_service.parse_dataset(ds_b)
        await graph_service.save_hierarchy(hier_b)

        # Link Study B to Clinic B Group
        link_query_b = """
        MATCH (s:Study {study_instance_uid: $study_uid})
        MATCH (g:Group {id: $group_id})
        MERGE (s)-[r:PERFORMED_AT {id: $rel_id}]->(g)
        ON CREATE SET r.test_id = $test_id
        """
        await graph_service.client.execute_query(
            link_query_b,
            {
                "study_uid": study_b_uid,
                "group_id": clinic_b_grp["id"],
                "rel_id": f"rel_perf_{uuid.uuid4().hex[:8]}",
                "test_id": test_id,
            },
        )

        # 4. Generate JWT tokens for each principal
        token_ent_admin = create_access_token(
            {"sub": enterprise_admin["id"], "email": enterprise_admin["primary_email"], "roles": ["PLATFORM_ADMIN"]}
        )
        token_hosp_sup = create_access_token(
            {"sub": hosp_supervisor["id"], "email": hosp_supervisor["primary_email"], "roles": ["USER"]}
        )
        token_nurse_a = create_access_token(
            {"sub": clinic_a_nurse["id"], "email": clinic_a_nurse["primary_email"], "roles": ["USER"]}
        )
        token_nurse_b = create_access_token(
            {"sub": clinic_b_nurse["id"], "email": clinic_b_nurse["primary_email"], "roles": ["USER"]}
        )

        # 5. Verify Transitive Downward Group Resolution (Graph Service)
        groups_ent = await group_service.get_effective_accessible_groups(enterprise_admin["id"])
        group_ids_ent = {g["id"] for g in groups_ent}
        assert enterprise_grp["id"] in group_ids_ent
        assert hospital_grp["id"] in group_ids_ent
        assert clinic_a_grp["id"] in group_ids_ent
        assert clinic_b_grp["id"] in group_ids_ent

        groups_hosp = await group_service.get_effective_accessible_groups(hosp_supervisor["id"])
        group_ids_hosp = {g["id"] for g in groups_hosp}
        assert enterprise_grp["id"] not in group_ids_hosp
        assert hospital_grp["id"] in group_ids_hosp
        assert clinic_a_grp["id"] in group_ids_hosp
        assert clinic_b_grp["id"] in group_ids_hosp

        groups_a = await group_service.get_effective_accessible_groups(clinic_a_nurse["id"])
        group_ids_a = {g["id"] for g in groups_a}
        assert group_ids_a == {clinic_a_grp["id"]}

        groups_b = await group_service.get_effective_accessible_groups(clinic_b_nurse["id"])
        group_ids_b = {g["id"] for g in groups_b}
        assert group_ids_b == {clinic_b_grp["id"]}

        # 6. Penetration / Access Control Checks on Endpoints
        # A) Clinic A Nurse accessing Clinic A context -> 200 OK
        res_a_self = client.get(
            "/api/v1/graph/patients",
            headers={"Authorization": f"Bearer {token_nurse_a}", "X-Group-ID": clinic_a_grp["id"]},
        )
        assert res_a_self.status_code == 200

        # B) Penetration Attempt: Clinic A Nurse attempting to claim Clinic B context -> 403 Forbidden
        res_a_pen = client.get(
            "/api/v1/graph/patients",
            headers={"Authorization": f"Bearer {token_nurse_a}", "X-Group-ID": clinic_b_grp["id"]},
        )
        assert res_a_pen.status_code == 403
        assert "does not have active access" in res_a_pen.json().get("detail", "")

        # C) Penetration Attempt: Clinic B Nurse attempting to claim Clinic A context -> 403 Forbidden
        res_b_pen = client.get(
            "/api/v1/graph/patients",
            headers={"Authorization": f"Bearer {token_nurse_b}", "X-Group-ID": clinic_a_grp["id"]},
        )
        assert res_b_pen.status_code == 403
        assert "does not have active access" in res_b_pen.json().get("detail", "")

        # D) Hospital Supervisor accessing both child clinics -> 200 OK
        res_hosp_a = client.get(
            "/api/v1/graph/patients",
            headers={"Authorization": f"Bearer {token_hosp_sup}", "X-Group-ID": clinic_a_grp["id"]},
        )
        assert res_hosp_a.status_code == 200

        res_hosp_b = client.get(
            "/api/v1/graph/patients",
            headers={"Authorization": f"Bearer {token_hosp_sup}", "X-Group-ID": clinic_b_grp["id"]},
        )
        assert res_hosp_b.status_code == 200

        # E) Enterprise Admin accessing any facility -> 200 OK
        res_admin_a = client.get(
            "/api/v1/graph/patients",
            headers={"Authorization": f"Bearer {token_ent_admin}", "X-Group-ID": clinic_a_grp["id"]},
        )
        assert res_admin_a.status_code == 200

        res_admin_b = client.get(
            "/api/v1/graph/patients",
            headers={"Authorization": f"Bearer {token_ent_admin}", "X-Group-ID": clinic_b_grp["id"]},
        )
        assert res_admin_b.status_code == 200

    finally:
        await graph_service.cleanup_test_data(test_id)
