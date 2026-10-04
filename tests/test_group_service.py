import uuid

import pytest

from db.neo4j import neo4j_client
from service.graph_service import graph_service
from service.group_service import group_service


@pytest.mark.anyio
async def test_group_crud_and_hierarchy():
    test_id = f"test_grp_{uuid.uuid4().hex[:8]}"

    # Pre-test cleanup
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        # 1. Create Top-Level Enterprise Group
        enterprise = await group_service.create_group(
            name="Bay Health Enterprise",
            code=f"BHE_{uuid.uuid4().hex[:4]}",
            group_type="HEALTH_SYSTEM",
            description="Top-level health system",
            test_id=test_id,
        )
        assert enterprise["name"] == "Bay Health Enterprise"
        assert enterprise["group_type"] == "HEALTH_SYSTEM"
        assert enterprise["is_active"] is True

        # 2. Create Hospital Sub-Group
        hospital = await group_service.create_group(
            name="Peninsula Regional Hospital",
            code=f"PRH_{uuid.uuid4().hex[:4]}",
            group_type="HOSPITAL",
            test_id=test_id,
        )

        # 3. Create Leaf Clinic Sub-Group
        clinic = await group_service.create_group(
            name="Half Moon Bay Clinic",
            code=f"HMB_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )

        # 4. Build hierarchy: Clinic -> Hospital -> Enterprise
        nest_1 = await group_service.nest_group(
            child_group_id=clinic["id"],
            parent_group_id=hospital["id"],
            test_id=test_id,
        )
        assert nest_1["child_id"] == clinic["id"]
        assert nest_1["parent_id"] == hospital["id"]

        nest_2 = await group_service.nest_group(
            child_group_id=hospital["id"],
            parent_group_id=enterprise["id"],
            test_id=test_id,
        )
        assert nest_2["child_id"] == hospital["id"]

        # 5. Fetch by ID and Code
        fetched_grp = await group_service.get_group_by_id(clinic["id"])
        assert fetched_grp is not None
        assert fetched_grp["id"] == clinic["id"]

        fetched_by_code = await group_service.get_group_by_code(clinic["code"])
        assert fetched_by_code is not None
        assert fetched_by_code["id"] == clinic["id"]

        # 6. List groups filtered by test_id
        grps = await group_service.list_groups(test_id=test_id)
        assert len(grps) == 3

    finally:
        # Post-test cleanup
        await neo4j_client.close()


@pytest.mark.anyio
async def test_group_affiliation_and_transitive_rebac_resolution():
    test_id = f"test_rebac_{uuid.uuid4().hex[:8]}"

    # Pre-test cleanup
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    try:
        # Create hierarchy: Enterprise -> Hospital -> Clinic
        enterprise = await group_service.create_group(
            name="Alpha Health Enterprise",
            code=f"ALP_ENT_{uuid.uuid4().hex[:4]}",
            group_type="HEALTH_SYSTEM",
            test_id=test_id,
        )
        hospital = await group_service.create_group(
            name="Alpha Regional Hospital",
            code=f"ALP_HOS_{uuid.uuid4().hex[:4]}",
            group_type="HOSPITAL",
            test_id=test_id,
        )
        clinic = await group_service.create_group(
            name="Alpha Coast Clinic",
            code=f"ALP_CLI_{uuid.uuid4().hex[:4]}",
            group_type="FACILITY",
            test_id=test_id,
        )

        await group_service.nest_group(clinic["id"], hospital["id"], test_id=test_id)
        await group_service.nest_group(hospital["id"], enterprise["id"], test_id=test_id)

        # Create simulated users in graph directly for affiliation testing
        admin_user_id = f"usr_admin_{uuid.uuid4().hex[:6]}"
        nurse_user_id = f"usr_nurse_{uuid.uuid4().hex[:6]}"

        await neo4j_client.execute_query(
            """
            CREATE (u1:User {
                id: $u1_id, display_name: 'Enterprise Admin', primary_email: $admin_email,
                is_active: true, test_id: $test_id
            })
            CREATE (u2:User {
                id: $u2_id, display_name: 'Clinic Nurse', primary_email: $nurse_email,
                is_active: true, test_id: $test_id
            })
            """,
            {
                "u1_id": admin_user_id,
                "admin_email": f"admin_{uuid.uuid4().hex[:6]}@alpha.org",
                "u2_id": nurse_user_id,
                "nurse_email": f"nurse_{uuid.uuid4().hex[:6]}@alpha.org",
                "test_id": test_id,
            },
        )

        # Affiliate Admin with Top-Level Enterprise
        await group_service.add_user_to_group(
            user_id=admin_user_id,
            group_id=enterprise["id"],
            roles=["Admin"],
            status="ACTIVE",
            is_primary=True,
            test_id=test_id,
        )

        # Affiliate Nurse with Leaf Clinic
        await group_service.add_user_to_group(
            user_id=nurse_user_id,
            group_id=clinic["id"],
            roles=["Nurse", "Viewer"],
            status="ACTIVE",
            is_primary=True,
            test_id=test_id,
        )

        # Check Admin's effective accessible groups (must include Enterprise, Hospital, Clinic)
        admin_accessible = await group_service.get_effective_accessible_groups(admin_user_id)
        admin_grp_ids = {g["id"] for g in admin_accessible}
        assert enterprise["id"] in admin_grp_ids
        assert hospital["id"] in admin_grp_ids
        assert clinic["id"] in admin_grp_ids
        assert len(admin_accessible) == 3

        # Check Nurse's effective accessible groups (must strictly include ONLY Clinic)
        nurse_accessible = await group_service.get_effective_accessible_groups(nurse_user_id)
        nurse_grp_ids = {g["id"] for g in nurse_accessible}
        assert clinic["id"] in nurse_grp_ids
        assert enterprise["id"] not in nurse_grp_ids
        assert hospital["id"] not in nurse_grp_ids
        assert len(nurse_accessible) == 1

        # Deactivate Nurse's affiliation and verify instant cutoff
        await group_service.update_user_group_status(
            user_id=nurse_user_id,
            group_id=clinic["id"],
            status="INACTIVE",
        )
        deactivated_nurse_accessible = await group_service.get_effective_accessible_groups(nurse_user_id)
        assert len(deactivated_nurse_accessible) == 0

    finally:
        await neo4j_client.close()
