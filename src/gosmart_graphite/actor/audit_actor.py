import threading
from typing import Any, Dict, Optional, Union

import pykka

from gosmart_graphite.core.config import get_settings
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model.audit import AuditAction, AuditNode, AuditSeverity

logger = get_logger("gosmart_gs.audit")


class AuditActor(pykka.ThreadingActor):
    """Pykka Actor for processing, logging, and persisting audit events.

    Reliability Policy:
    Always logs the incoming event with the appropriate severity FIRST before
    attempting database persistence to Neo4j. This ensures that even during
    database outages, audit events are preserved in the rotating log backup.
    """

    use_daemon_thread = True

    def __init__(self) -> None:
        super().__init__()
        self.settings = get_settings()
        self.processed_count: int = 0
        self.last_status: str = "INITIALIZED"

    def on_start(self) -> None:
        logger.info("audit_actor_started", actor_urn=self.actor_ref.actor_urn)

    def on_stop(self) -> None:
        logger.info("audit_actor_stopped", actor_urn=self.actor_ref.actor_urn)

    def on_receive(self, message: Union[Dict[str, Any], AuditNode]) -> Any:
        # Handle AuditNode object directly
        if isinstance(message, AuditNode):
            return self._handle_audit_node(message)

        if not isinstance(message, dict):
            logger.warning("audit_unsupported_message_type", msg_type=type(message).__name__)
            return {"success": False, "error": f"Unsupported message type: {type(message).__name__}"}

        msg_type = message.get("type", "AUDIT").upper()

        if msg_type == "PING":
            return {"status": "PONG", "processed_count": self.processed_count}

        if msg_type == "STATUS":
            return {
                "processed_count": self.processed_count,
                "last_status": self.last_status,
            }

        if msg_type in ("AUDIT", "AUDIT_EVENT"):
            return self._handle_audit_dict(message)

        # Fallback if dictionary has action and actorId directly
        if "action" in message and ("actorId" in message or "actor_id" in message):
            return self._handle_audit_dict(message)

        logger.warning("audit_unrecognized_message", message=message)
        return {"error": f"Unknown message type: {msg_type}"}

    def _log_event(self, node: AuditNode) -> None:
        """Log the audit event to system log using structured logging with corresponding severity."""
        log_payload = {
            "audit_action": node.action,
            "status_code": node.statusCode,
            "status_message": node.statusMessage,
            "severity": node.severity,
            "actor_id": node.actorId,
            "actor": node.actor,
            "target_id": node.targetId,
            "target": node.target,
            "result": node.result,
            "details": node.details,
            "test_id": node.test_id,
            "audit_id": node.id,
        }
        sev = (node.severity or "INFO").upper()
        if sev == "CRITICAL":
            logger.critical("audit_event", **log_payload)
        elif sev == "ERROR":
            logger.error("audit_event", **log_payload)
        elif sev == "WARNING":
            logger.warning("audit_event", **log_payload)
        else:
            logger.info("audit_event", **log_payload)

    def _handle_audit_node(self, node: AuditNode) -> Dict[str, Any]:
        """Process an AuditNode: log first, then persist to Neo4j."""
        # 1. ALWAYS LOG FIRST (backup guarantee)
        self._log_event(node)

        # 2. Persist to Neo4j if enabled
        db_result = {"id": node.id, "success": True}
        if getattr(self.settings, "AUDIT_LOG_TO_DB", True):
            try:
                from gosmart_graphite.service.graph_service import graph_service

                db_result = graph_service.save_audit_log_sync(node)
            except Exception as e:
                logger.warning("audit_db_persistence_failed", audit_id=node.id, error=str(e), exc_info=True)
                db_result = {"id": node.id, "success": False, "error": str(e)}

        self.processed_count += 1
        self.last_status = "SUCCESS" if db_result.get("success") else "DB_FAILED_LOGGED"
        return {"success": True, "id": node.id, "db_result": db_result}

    def _handle_audit_dict(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Convert dictionary payload to AuditNode, then log and persist."""
        payload = msg.get("payload", msg) if "payload" in msg and isinstance(msg.get("payload"), dict) else msg
        action = payload.get("action")
        if isinstance(action, AuditAction):
            action = action.value

        actor_id = payload.get("actorId") or payload.get("actor_id") or "system:gosmart"
        actor = payload.get("actor") or "system"
        target_id = payload.get("targetId") or payload.get("target_id")
        target = payload.get("target")
        status_code = payload.get("statusCode") or payload.get("status_code", 200)
        status_message = payload.get("statusMessage") or payload.get("status_message", "OK")
        severity = payload.get("severity", "INFO")
        result = payload.get("result", "")
        details = payload.get("details")
        test_id = payload.get("test_id")
        event_id = payload.get("event_id")

        try:
            node = AuditNode(
                action=action,
                statusCode=int(status_code),
                statusMessage=str(status_message),
                severity=severity,
                actorId=str(actor_id),
                actor=str(actor),
                targetId=str(target_id) if target_id else None,
                target=str(target) if target else None,
                result=str(result),
                details=details,
                test_id=test_id,
                event_id=event_id,
            )
            return self._handle_audit_node(node)
        except Exception as e:
            logger.error("audit_node_creation_failed", error=str(e), exc_info=True, raw_message=msg)
            self.last_status = f"ERROR: {str(e)}"
            return {"success": False, "error": str(e)}


_audit_actor_ref: Optional[pykka.ActorRef] = None
_audit_actor_lock = threading.Lock()


def get_audit_actor() -> pykka.ActorRef:
    """Get or start the singleton AuditActor instance."""
    global _audit_actor_ref
    with _audit_actor_lock:
        if _audit_actor_ref is None or not _audit_actor_ref.is_alive():
            _audit_actor_ref = AuditActor.start()
        return _audit_actor_ref


def stop_audit_actor() -> None:
    """Stop the singleton AuditActor if running."""
    global _audit_actor_ref
    with _audit_actor_lock:
        if _audit_actor_ref is not None:
            try:
                _audit_actor_ref.stop()
            except Exception:
                pass
            _audit_actor_ref = None


def record_audit(
    action: Union[str, AuditAction],
    actor_id: str,
    actor: str,
    target_id: Optional[str] = None,
    target: Optional[str] = None,
    status_code: int = 200,
    status_message: str = "OK",
    severity: AuditSeverity = "INFO",
    result: str = "",
    details: Optional[Any] = None,
    test_id: Optional[str] = None,
    event_id: Optional[str] = None,
    fire_and_forget: bool = True,
) -> Optional[Dict[str, Any]]:
    """Convenience helper to record an audit event across the system.

    By default, uses fire-and-forget (tell) for non-blocking execution.
    Set fire_and_forget=False for synchronous confirmation (ask).
    """
    msg = {
        "type": "AUDIT",
        "action": action.value if isinstance(action, AuditAction) else action,
        "actorId": actor_id,
        "actor": actor,
        "targetId": target_id,
        "target": target,
        "statusCode": status_code,
        "statusMessage": status_message,
        "severity": severity,
        "result": result,
        "details": details,
        "test_id": test_id,
        "event_id": event_id,
    }
    try:
        actor_ref = get_audit_actor()
        if fire_and_forget:
            actor_ref.tell(msg)
            return None
        else:
            return actor_ref.ask(msg, timeout=5.0)
    except Exception as e:
        logger.error("record_audit_failed", action=str(action), error=str(e), exc_info=True)
        return {"success": False, "error": str(e)}
