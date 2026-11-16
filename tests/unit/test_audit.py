import uuid
from datetime import datetime, timedelta, timezone

import pytest

from gosmart_graphite.actor.audit_actor import get_audit_actor, record_audit, stop_audit_actor
from gosmart_graphite.core.config import get_settings
from gosmart_graphite.model.audit import AuditAction, AuditNode


@pytest.fixture(scope="module", autouse=True)
def teardown_module_actors():
    """Ensure all background actor threads are stopped after test module execution."""
    yield
    stop_audit_actor()


def test_audit_node_validation_and_expiration():
    """Test AuditNode creation, default 1-year expiration, and JSON details serialization."""
    settings = get_settings()
    before_creation = datetime.now(timezone.utc)

    node = AuditNode(
        action=AuditAction.USER_DISABLED,
        statusCode=200,
        statusMessage="User successfully disabled",
        severity="WARNING",
        actorId="user:admin_01",
        actor="admin@gosmart.health",
        targetId="user:radiologist_02",
        target="Dr. Bob Smith",
        result="Account disabled by administrator",
        details={"reason": "extended medical leave", "ticket": "GS-1029"},
        test_id="test_node_validation",
    )

    assert node.action == "User Disabled"
    assert node.statusCode == 200
    assert node.severity == "WARNING"
    assert node.actorId == "user:admin_01"
    assert node.targetId == "user:radiologist_02"
    assert node.id != ""
    assert '"extended medical leave"' in node.details

    # Verify expiration is approximately 365 days into the future
    assert node.ExpiresAt is not None
    expected_expiry = before_creation + timedelta(days=settings.AUDIT_RETENTION_DAYS)
    diff = abs((node.ExpiresAt - expected_expiry).total_seconds())
    assert diff < 60  # Within 1 minute tolerance


def test_audit_node_deterministic_id_idempotency():
    """Test deterministic ID calculation for AuditNode."""
    created_time = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)

    node1 = AuditNode(
        action=AuditAction.DICOM_C_STORE_INGESTED,
        statusCode=200,
        actorId="ae:MODALITY_CT",
        actor="MODALITY_CT",
        targetId="instance:1.2.840.10008.1.1",
        target="SOP Instance 1.2.840.10008.1.1",
        result="Ingestion success",
        CreatedAt=created_time,
        event_id="evt_unique_1001",
        test_id="test_idempotency",
    )

    node2 = AuditNode(
        action=AuditAction.DICOM_C_STORE_INGESTED,
        statusCode=200,
        actorId="ae:MODALITY_CT",
        actor="MODALITY_CT",
        targetId="instance:1.2.840.10008.1.1",
        target="SOP Instance 1.2.840.10008.1.1",
        result="Ingestion success",
        CreatedAt=created_time,
        event_id="evt_unique_1001",
        test_id="test_idempotency",
    )

    assert node1.id == node2.id
    assert len(node1.id) == 36


def test_audit_actor_database_failure_resilience():
    """Test that AuditActor logs event before DB write and survives DB exceptions gracefully."""
    from unittest.mock import patch

    actor = get_audit_actor()
    test_id = f"test_resilience_{uuid.uuid4().hex[:6]}"
    with patch(
        "gosmart_graphite.service.graph_service.GraphService.save_audit_log_sync",
        side_effect=RuntimeError("Neo4j connection lost"),
    ):
        # Synchronous ask when DB is down
        res = record_audit(
            action=AuditAction.USER_LOGIN_FAILED,
            actor_id="user:intruder",
            actor="intruder@malicious.com",
            status_code=401,
            status_message="Invalid credentials",
            severity="WARNING",
            test_id=test_id,
            fire_and_forget=False,
        )
        assert res is not None
        assert res.get("success") is True
        assert res.get("db_result", {}).get("success") is False

    # Check actor status
    status = actor.ask({"type": "STATUS"})
    assert status.get("last_status") == "DB_FAILED_LOGGED"
    assert actor.is_alive()
