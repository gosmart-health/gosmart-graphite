"""Integration tests for audit persistence and actor-driven audit events."""

import time
import uuid

import pytest

from gosmart_graphite.actor.audit_actor import get_audit_actor, record_audit, stop_audit_actor
from gosmart_graphite.actor.dicom_actor import stop_dicom_actor
from gosmart_graphite.model.audit import AuditAction, AuditNode
from gosmart_graphite.service.graph_service import graph_service


@pytest.fixture(scope="module", autouse=True)
def teardown_module_actors():
    """Stop background actor threads after this integration module."""
    yield
    stop_audit_actor()
    stop_dicom_actor()


@pytest.mark.anyio
async def test_audit_graph_service_crud_and_search():
    """Test persisting and searching AuditLog nodes in Neo4j."""
    test_id = f"test_audit_crud_{uuid.uuid4().hex[:6]}"
    await graph_service.cleanup_test_data(test_id)
    await graph_service.init_schema_constraints()

    node = AuditNode(
        action=AuditAction.GROUP_CREATED,
        statusCode=201,
        statusMessage="Radiology Department Created",
        severity="INFO",
        actorId="user:sys_admin",
        actor="admin@gosmart.health",
        targetId="group:RAD_DEPT",
        target="Radiology Dept",
        result="Group created",
        details={"code": "RAD_DEPT", "type": "DEPARTMENT"},
        test_id=test_id,
    )

    save_res = await graph_service.save_audit_log(node)
    assert save_res.get("success") is True

    # Search audit log
    results = await graph_service.search_audit_logs(
        action=AuditAction.GROUP_CREATED.value,
        actor_id="user:sys_admin",
        include_tests=True,
        test_id=test_id,
    )
    assert len(results) >= 1
    found = results[0]
    assert found["action"] == "Group Created"
    assert found["statusCode"] == 201
    assert found["actorId"] == "user:sys_admin"
    assert found["targetId"] == "group:RAD_DEPT"


def test_audit_actor_synchronous_and_async():
    """Test AuditActor via ask (sync) and tell (fire-and-forget)."""
    test_id = f"test_actor_{uuid.uuid4().hex[:6]}"
    actor = get_audit_actor()
    assert actor.is_alive()

    # 1. Test ping
    pong = actor.ask({"type": "PING"})
    assert pong.get("status") == "PONG"

    # 2. Test synchronous audit event
    res = record_audit(
        action=AuditAction.USER_CHANGED,
        actor_id="user:admin",
        actor="admin@gosmart.health",
        target_id="user:nurse_01",
        target="Nurse Jane",
        status_code=200,
        status_message="Roles updated",
        severity="INFO",
        result="User updated",
        test_id=test_id,
        fire_and_forget=False,
    )
    assert res is not None
    assert res.get("success") is True

    # 3. Test fire-and-forget audit event
    record_audit(
        action=AuditAction.DICOM_CONFIGURATION_CREATED,
        actor_id="user:admin",
        actor="admin@gosmart.health",
        target_id="config:mock_scp",
        target="Mock SCP",
        status_code=200,
        status_message="Configuration created",
        severity="INFO",
        test_id=test_id,
        fire_and_forget=True,
    )

    # Give actor a moment to process the message
    time.sleep(0.3)

    logs = graph_service.search_audit_logs_sync(
        actor_id="user:admin",
        include_tests=True,
        test_id=test_id,
    )
    actions = [log["action"] for log in logs]
    assert "User Changed" in actions
    assert "DICOM Configuration Created" in actions


@pytest.mark.anyio
async def test_dicom_c_store_records_audit():
    """Test that incoming DICOM C-STORE records an audit event."""
    from gosmart_graphite.service.dicom_service import dicom_service
    from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

    pid = f"PID_{uuid.uuid4().hex[:6]}"
    calling_aet = f"SCU_{uuid.uuid4().hex[:4]}"

    ds = dicom_service.create_mock_dicom(patient_id=pid, modality="OT")
    sop_uid = str(ds.SOPInstanceUID)

    class MockRequestor:
        address = "127.0.0.1"
        ae_title = calling_aet

    class MockAcceptor:
        address = "127.0.0.1"
        port = 11112
        ae_title = "GOSMART_GS"

    class MockAssoc:
        requestor = MockRequestor()
        acceptor = MockAcceptor()

    mock_event = type(
        "MockEvent",
        (),
        {
            "request": type("Req", (), {"AffectedSOPInstanceUID": sop_uid})(),
            "dataset": ds,
            "file_meta": ds.file_meta,
            "assoc": MockAssoc(),
        },
    )()

    # Process C-STORE
    status_code = pynetdicom_service.handle_c_store(mock_event)
    assert status_code == 0x0000

    time.sleep(0.3)

    # Verify AuditLog created
    records = await graph_service.search_audit_logs(
        action=AuditAction.DICOM_C_STORE_INGESTED.value,
        actor_id=f"ae:{calling_aet}",
        include_tests=True,
    )
    assert len(records) >= 1
    audit_rec = records[0]
    assert audit_rec["actor"] == calling_aet
    assert audit_rec["statusCode"] == 200
    assert audit_rec["targetId"] == f"instance:{sop_uid}"


def test_dicom_c_echo_records_audit():
    """Test that incoming C-ECHO verification records an audit event."""
    from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

    echo_aet = f"ECHO_{uuid.uuid4().hex[:4]}"

    class MockRequestor:
        ae_title = echo_aet

    class MockAssoc:
        requestor = MockRequestor()

    class MockEchoEvent:
        assoc = MockAssoc()

    status = pynetdicom_service.handle_c_echo(MockEchoEvent())
    assert status == 0x0000

    time.sleep(0.3)

    logs = graph_service.search_audit_logs_sync(
        action=AuditAction.DICOM_C_ECHO_VERIFIED.value,
        actor_id=f"ae:{echo_aet}",
        include_tests=True,
    )
    assert len(logs) >= 1
    assert logs[0]["actor"] == echo_aet
    assert logs[0]["statusCode"] == 200


def test_dicom_actor_mock_ingestion_records_audit():
    """Test that DicomIngestionActor records audit event on mock ingestion."""
    from gosmart_graphite.actor.dicom_actor import get_dicom_actor

    ingest_actor = get_dicom_actor()
    test_id = f"test_ingest_{uuid.uuid4().hex[:6]}"
    patient_id = f"AUDIT_INGEST_{uuid.uuid4().hex[:6]}"

    result = ingest_actor.ask(
        {
            "type": "INGEST_MOCK",
            "patient_id": patient_id,
            "modality": "CT",
            "test_id": test_id,
            "acted_by": "audit_test_suite",
        },
        timeout=10.0,
    )
    assert result.get("success") is True

    time.sleep(0.3)

    logs = graph_service.search_audit_logs_sync(
        action=AuditAction.DATASET_MOCK_INGESTED.value,
        include_tests=True,
        test_id=test_id,
    )
    assert len(logs) >= 1
    assert logs[0]["action"] == "Dataset Mock Ingested"
    assert logs[0]["statusCode"] == 200
    assert logs[0]["actor"] == "audit_test_suite"


def test_dicom_c_find_records_audit():
    """Test that C-FIND execution records an audit event."""
    from pydicom.dataset import Dataset

    from gosmart_graphite.service.pynetdicom_service import pynetdicom_service

    find_aet = f"FIND_{uuid.uuid4().hex[:4]}"

    class MockRequestor:
        ae_title = find_aet

    class MockAssoc:
        requestor = MockRequestor()

    identifier = Dataset()
    identifier.QueryRetrieveLevel = "STUDY"
    identifier.PatientID = "*"

    mock_event = type(
        "MockEvent",
        (),
        {
            "identifier": identifier,
            "assoc": MockAssoc(),
            "is_cancelled": False,
        },
    )()

    results = list(pynetdicom_service.handle_c_find(mock_event))
    assert len(results) >= 1

    time.sleep(0.3)

    logs = graph_service.search_audit_logs_sync(
        action=AuditAction.DICOM_C_FIND_EXECUTED.value,
        actor_id=f"ae:{find_aet}",
        include_tests=True,
    )
    assert len(logs) >= 1
    assert logs[0]["actor"] == find_aet
    assert logs[0]["statusCode"] == 200
