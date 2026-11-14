import uuid
from datetime import datetime, timezone

import pytest

from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model.graph import (
    DICOMConfigurationNode,
    HasDicomConfigurationRelation,
    SystemNode,
    generate_deterministic_id,
)
from gosmart_graphite.model.system import (
    DICOMConfigurationCreateRequest,
    DICOMConfigurationUpdateRequest,
    SystemUpdateRequest,
)
from gosmart_graphite.service.system_service import system_service


def test_system_node_unit():
    """Verify SystemNode creation, deterministic ID, and properties."""
    sys = SystemNode(
        name="Test VNA",
        owner="Test Hospital",
        address="123 Ocean Ave",
        email="vna@testhospital.org",
        phone="+1 555 123 4567",
        serial_number="TEST-SN-001",
    )
    assert sys.__label__ == "System"
    assert sys.id != ""
    assert sys.name == "Test VNA"
    assert sys.owner == "Test Hospital"
    assert sys.address == "123 Ocean Ave"
    assert sys.email == "vna@testhospital.org"
    assert sys.phone == "+1 555 123 4567"
    assert sys.serial_number == "TEST-SN-001"
    assert isinstance(sys.installed_at, datetime)

    # Verify deterministic ID repeatability
    sys2 = SystemNode()
    assert sys.id == sys2.id, "Singleton System node without test_id must have identical deterministic ID"


def test_dicom_configuration_node_unit():
    """Verify DICOMConfigurationNode promiscuous mode and deterministic ID."""
    cfg_promiscuous = DICOMConfigurationNode(ae_title="*", port=11112)
    assert cfg_promiscuous.__label__ == "DICOMConfiguration"
    assert cfg_promiscuous.is_promiscuous is True
    assert cfg_promiscuous.id != ""

    cfg_specific = DICOMConfigurationNode(ae_title="CT_SCANNER", host="192.168.1.50", port=104)
    assert cfg_specific.is_promiscuous is False
    assert cfg_specific.ae_title == "CT_SCANNER"

    # Deterministic ID check
    expected_id = generate_deterministic_id(
        {"entity": "DICOMConfiguration", "ae_title": "CT_SCANNER", "host": "192.168.1.50", "port": 104}
    )
    assert cfg_specific.id == expected_id


def test_has_dicom_configuration_relation_unit():
    """Verify HasDicomConfigurationRelation type and soft-delete negation helpers."""
    rel = HasDicomConfigurationRelation(start_node_id="sys_1", end_node_id="cfg_1")
    assert rel.__type__ == "HAS_DICOM_CONFIGURATION"
    assert rel.to_negated_type() == "NOT_HAS_DICOM_CONFIGURATION"
    assert rel.id != ""


@pytest.mark.anyio
async def test_system_service_bootstrap_and_crud():
    """Integration test verifying SystemService bootstrapping, CRUD, soft-delete, and restore."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    test_id = f"test_sys_{uuid.uuid4().hex[:8]}"

    # Per AGENTS.md rule: Clean up pre-existing test data BEFORE the test
    await system_service.cleanup_test_data(test_id)

    # 1. Bootstrap System & Promiscuous Configuration
    boot_res = await system_service.bootstrap_system(acted_by="test_installer", test_id=test_id)
    assert boot_res["success"] is True
    sys_obj = boot_res["system"]
    assert sys_obj["name"] == "GoSmart Health VNA"
    assert sys_obj["owner"] == "GoSmart Health"
    assert sys_obj["email"] == "admin@gosmart.health"
    assert sys_obj["phone"] == "+1 (650) 535-0097"
    assert sys_obj["serial_number"] == "GSH-VNA-0001"
    assert sys_obj["test_id"] == test_id

    prom_cfg = boot_res["promiscuous_config"]
    assert prom_cfg["ae_title"] == "*"
    assert prom_cfg["is_promiscuous"] is True
    assert "SCP" in prom_cfg["roles"]

    # 2. Test Idempotency of Bootstrap
    boot_res2 = await system_service.bootstrap_system(acted_by="test_installer", test_id=test_id)
    assert boot_res2["success"] is True
    assert boot_res2["system"]["id"] == sys_obj["id"]

    # 3. Update System Node
    update_data = SystemUpdateRequest(
        name="Updated Regional Medical Center VNA",
        owner="Regional Health System",
        address="456 Healthcare Way, San Francisco, CA",
        email="pacs_admin@regionalhealth.org",
        phone="+1 415 555 9999",
        serial_number="RHC-VNA-9999",
        installed_at=datetime.now(timezone.utc),
    )
    updated_sys = await system_service.update_system(update_data, acted_by="test_installer", test_id=test_id)
    assert updated_sys is not None
    assert updated_sys["name"] == "Updated Regional Medical Center VNA"
    assert updated_sys["owner"] == "Regional Health System"
    assert updated_sys["email"] == "pacs_admin@regionalhealth.org"

    # 4. Create New Additional DICOM Configuration
    new_cfg_req = DICOMConfigurationCreateRequest(
        ae_title="ORTHO_PACS",
        roles=["SCP", "SCU"],
        host="10.0.1.100",
        port=11114,
        dicom_services=["Verification", "Storage"],
        transfer_syntaxes=["1.2.840.10008.1.2.1"],
        description="Orthopedics Dedicated PACS Node",
        test_id=test_id,
    )
    created_cfg = await system_service.create_configuration(
        config_data=new_cfg_req, acted_by="test_installer", system_id=sys_obj["id"]
    )
    assert created_cfg["ae_title"] == "ORTHO_PACS"
    assert "SCU" in created_cfg["roles"]
    assert created_cfg["port"] == 11114
    config_id = created_cfg["id"]

    # 5. List Configurations
    configs = await system_service.list_configurations(system_id=sys_obj["id"], include_tests=True, test_id=test_id)
    assert len(configs) >= 2
    ae_titles = [c["ae_title"] for c in configs]
    assert "*" in ae_titles
    assert "ORTHO_PACS" in ae_titles

    # 6. Retrieve Configuration
    fetched_cfg = await system_service.get_configuration(config_id=config_id)
    assert fetched_cfg is not None
    assert fetched_cfg["ae_title"] == "ORTHO_PACS"

    # 7. Update Configuration
    up_cfg_req = DICOMConfigurationUpdateRequest(description="Updated Ortho PACS with Extended Storage")
    updated_cfg = await system_service.update_configuration(
        config_id=config_id, update_data=up_cfg_req, acted_by="test_installer"
    )
    assert updated_cfg is not None
    assert updated_cfg["description"] == "Updated Ortho PACS with Extended Storage"

    # 8. Soft Delete Configuration
    del_ok = await system_service.delete_configuration(
        config_id=config_id, acted_by="test_installer", hard_delete=False
    )
    assert del_ok is True

    # Confirm it is no longer returned in active list
    configs_after_del = await system_service.list_configurations(
        system_id=sys_obj["id"], include_tests=True, test_id=test_id
    )
    assert config_id not in [c["id"] for c in configs_after_del]

    # 9. Restore Configuration
    restored_cfg = await system_service.restore_configuration(config_id=config_id, acted_by="test_installer")
    assert restored_cfg is not None
    assert restored_cfg["id"] == config_id

    configs_after_restore = await system_service.list_configurations(
        system_id=sys_obj["id"], include_tests=True, test_id=test_id
    )
    assert config_id in [c["id"] for c in configs_after_restore]


@pytest.mark.anyio
async def test_empty_string_test_id_normalization():
    """Verify that passing test_id='' normalizes to None in models and queries."""
    req = DICOMConfigurationCreateRequest(
        ae_title="NORM_TEST",
        roles=["SCU"],
        host="192.168.1.99",
        port=104,
        test_id="",
    )
    assert req.test_id is None

    node = DICOMConfigurationNode(
        ae_title="NORM_TEST",
        test_id="   ",
    )
    assert node.test_id is None

    # Verify query handling with empty string vs None
    sys_none = await system_service.get_system(test_id=None)
    sys_empty = await system_service.get_system(test_id="")
    assert sys_none is not None
    assert sys_empty is not None
    assert sys_none["id"] == sys_empty["id"]


@pytest.mark.anyio
async def test_system_service_cleanup_all_test_data():
    """Verify cleanup_all_test_data across all target node labels."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    test_id = f"test_cleanup_{uuid.uuid4().hex[:8]}"

    # Pre-clean before test
    await system_service.cleanup_all_test_data(test_id=test_id)

    # Seed test nodes across Patient, Study, Series, Instance, DICOMConfiguration
    driver = await neo4j_client.get_async_driver()
    async with driver.session() as session:
        await session.run(
            """
            CREATE (p:Patient {id: $pid, patient_id: 'P_TEST', test_id: $test_id})
            CREATE (s:Study {id: $sid, study_instance_uid: '1.2.3', test_id: $test_id})
            CREATE (se:Series {id: $seid, series_instance_uid: '1.2.3.4', test_id: $test_id})
            CREATE (i:Instance {id: $iid, sop_instance_uid: '1.2.3.4.5', test_id: $test_id})
            CREATE (cfg:DICOMConfiguration {id: $cfgid, ae_title: 'TEST_AET', test_id: $test_id})
            """,
            pid=f"p_{test_id}",
            sid=f"s_{test_id}",
            seid=f"se_{test_id}",
            iid=f"i_{test_id}",
            cfgid=f"cfg_{test_id}",
            test_id=test_id,
        )

    # Run cleanup for this specific test_id
    res = await system_service.cleanup_all_test_data(test_id=test_id)
    assert res["success"] is True
    assert res["total_deleted"] >= 5
    assert "Patient" in res["deleted_by_label"]
    assert "Study" in res["deleted_by_label"]
    assert "Series" in res["deleted_by_label"]
    assert "Instance" in res["deleted_by_label"]
    assert "DICOMConfiguration" in res["deleted_by_label"]

    # Verify nodes are deleted
    async with driver.session() as session:
        result = await session.run("MATCH (n {test_id: $test_id}) RETURN count(n) AS cnt", test_id=test_id)
        rec = await result.single()
        assert rec["cnt"] == 0
