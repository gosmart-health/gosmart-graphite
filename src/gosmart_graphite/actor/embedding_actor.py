"""Pykka background actor for computing and registering decoupled vector embeddings in Neo4j."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional

import pykka
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from pydantic import ValidationError

from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model.actor_message import EmbeddingMessage, EmbeddingMethod
from gosmart_graphite.model.auth import Group
from gosmart_graphite.model.result import STATUS_OK
from gosmart_graphite.service.diagnostic_report_service import DiagnosticReportService
from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG

logger = get_logger("gosmart_gs.embedding_actor")

EmbeddingMethodHandler = Callable[[EmbeddingMessage], Dict[str, Any]]


class EmbeddingActor(pykka.ThreadingActor):
    """Pykka Actor for computing vector embeddings and registering them in Neo4j.

    Dispatches incoming :class:`EmbeddingMessage` payloads according to ``message.method``:
    - ``EmbeddingMethod.DIAGNOSTIC_REPORT`` (default): Parses a FHIR R4B
      ``DiagnosticReport``, computes L2-normalized findings/conclusion embeddings,
      and atomically registers ``:Embedding`` nodes and ``:EMBEDS`` links via
      :class:`DiagnosticReportService`.

    Additional embedding methods or resource handlers can be registered via
    ``method_handlers`` or added to :class:`EmbeddingMethod`.
    """

    use_daemon_thread = True

    def __init__(
        self,
        diagnostic_report_service: Optional[DiagnosticReportService] = None,
        method_handlers: Optional[Dict[str, EmbeddingMethodHandler]] = None,
    ) -> None:
        super().__init__()
        self.processed_count: int = 0
        self.last_status: str = "INITIALIZED"
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._diagnostic_report_service: Optional[DiagnosticReportService] = diagnostic_report_service
        self._method_handlers: Dict[str, EmbeddingMethodHandler] = {
            str(EmbeddingMethod.DIAGNOSTIC_REPORT): self._embed_diagnostic_report,
        }
        if method_handlers:
            for key, handler in method_handlers.items():
                self._method_handlers[str(key)] = handler

    def on_start(self) -> None:
        logger.info("embedding_actor_started", actor_urn=self.actor_ref.actor_urn)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

    def on_stop(self) -> None:
        logger.info("embedding_actor_stopped", actor_urn=self.actor_ref.actor_urn)
        if self._loop and not self._loop.is_closed():
            try:
                from gosmart_graphite.db.neo4j import neo4j_client

                self._loop.run_until_complete(neo4j_client.close_current_loop_driver())
            except Exception as why:
                logger.error("Failed to close Embedding actor Neo4j async driver", why=why)
            self._loop.close()

    def _get_diagnostic_report_service(self) -> DiagnosticReportService:
        svc = self._diagnostic_report_service
        if svc is None:
            svc = DiagnosticReportService()
            self._diagnostic_report_service = svc
        return svc

    def _resolve_group_context(
        self,
        msg: EmbeddingMessage,
        svc: DiagnosticReportService,
    ) -> Optional[Group]:
        """Return explicit group_context if supplied, or best-effort resolve from group_id."""
        if msg.group_context is not None:
            return msg.group_context
        if not msg.group_id:
            return None
        try:
            grp_data = svc.group_service.resolve_target_group_sync(
                identifier=msg.group_id,
                test_id=msg.test_id,
            )
            if isinstance(grp_data, Group):
                return grp_data
            if isinstance(grp_data, dict) and grp_data.get("name") and grp_data.get("code"):
                return Group(**grp_data)
        except Exception as exc:
            logger.warning(
                "embedding_actor_group_id_resolve_failed",
                group_id=msg.group_id,
                test_id=msg.test_id,
                error=str(exc),
            )
        return None

    @staticmethod
    def _coerce_diagnostic_report(resource: Any) -> DiagnosticReport:
        """Validate/coerce a FHIR R4B DiagnosticReport instance or dict payload."""
        if isinstance(resource, DiagnosticReport):
            return resource
        if isinstance(resource, dict):
            return DiagnosticReport.model_validate(resource)
        raise ValueError(f"Expected FHIR R4B DiagnosticReport instance or dict payload, got {type(resource).__name__}.")

    def _embed_diagnostic_report(self, msg: EmbeddingMessage) -> Dict[str, Any]:
        """Parse a DiagnosticReport, compute section embeddings, and register them in Neo4j."""
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("EmbeddingActor asyncio event loop is not running.")

        report = self._coerce_diagnostic_report(msg.resource)
        report_id = getattr(report, "id", None)
        svc = self._get_diagnostic_report_service()
        group_context = self._resolve_group_context(msg, svc)

        report_texts = svc.parse_diagnostic_report(report, group_context=group_context)
        embed_res = self._loop.run_until_complete(
            svc.compute_embeddings(
                report_texts,
                tag=msg.tag,
                test_id=msg.test_id,
            )
        )
        if embed_res.status != STATUS_OK:
            logger.warning(
                "embedding_actor_compute_failed",
                method=str(msg.method),
                report_id=report_id,
                status=embed_res.status,
                message=embed_res.message,
                group_id=msg.group_id,
                test_id=msg.test_id,
            )
            return {
                "success": False,
                "method": str(msg.method),
                "report_id": report_id,
                "status": f"COMPUTE_FAILED:{embed_res.status}",
                "status_code": embed_res.status,
                "error": embed_res.message,
            }

        reg_res = svc.graph_register(
            report_texts,
            tag=msg.tag,
            test_id=msg.test_id,
            acted_by=msg.acted_by,
        )
        if reg_res.status != STATUS_OK:
            logger.warning(
                "embedding_actor_register_failed",
                method=str(msg.method),
                report_id=report_id,
                status=reg_res.status,
                message=reg_res.message,
                group_id=msg.group_id,
                test_id=msg.test_id,
            )
            return {
                "success": False,
                "method": str(msg.method),
                "report_id": report_id,
                "status": f"REGISTER_FAILED:{reg_res.status}",
                "status_code": reg_res.status,
                "error": reg_res.message,
            }

        registered = reg_res.value or []
        embedding_ids = [emb.id for emb in registered]
        logger.info(
            "embedding_actor_diagnostic_report_registered",
            method=str(msg.method),
            report_id=report_id,
            tag=msg.tag,
            group_id=report_texts.group_context.id if report_texts.group_context else msg.group_id,
            test_id=msg.test_id,
            registered_count=len(registered),
            embedding_ids=embedding_ids,
        )
        return {
            "success": True,
            "method": str(msg.method),
            "report_id": report_id,
            "status": "SUCCESS",
            "status_code": reg_res.status,
            "registered_count": len(registered),
            "embedding_ids": embedding_ids,
            "message": reg_res.message,
        }

    def on_receive(self, message: Any) -> Any:
        """Handle incoming EmbeddingMessage (or control/dict payload)."""
        if isinstance(message, EmbeddingMessage):
            emb_msg = message
        elif isinstance(message, dict):
            msg_type = message.get("type")
            if msg_type == "PING":
                return {"status": "PONG", "processed_count": self.processed_count}
            if msg_type == "STATUS":
                return {
                    "processed_count": self.processed_count,
                    "last_status": self.last_status,
                }
            try:
                emb_msg = EmbeddingMessage.model_validate(message)
            except ValidationError as exc:
                logger.warning("embedding_actor_invalid_message", error=str(exc))
                return {"success": False, "error": f"Invalid EmbeddingMessage: {exc}"}
        else:
            logger.warning("embedding_actor_unsupported_message_type", msg_type=type(message).__name__)
            return {"success": False, "error": f"Unsupported message type: {type(message).__name__}"}

        method_key = str(emb_msg.method)
        handler = self._method_handlers.get(method_key)
        if handler is None:
            logger.warning("embedding_actor_unknown_method", method=method_key)
            self.last_status = f"UNSUPPORTED_METHOD:{method_key}"
            self.processed_count += 1
            return {"success": False, "method": method_key, "error": f"Unsupported embedding method: {method_key}"}

        try:
            outcome = handler(emb_msg)
        except Exception as exc:
            logger.error(
                "embedding_actor_processing_failed",
                method=method_key,
                group_id=emb_msg.group_id,
                test_id=emb_msg.test_id,
                error=str(exc),
                exc_info=True,
            )
            self.last_status = f"ERROR: {exc}"
            self.processed_count += 1
            return {"success": False, "method": method_key, "error": str(exc)}

        self.last_status = str(outcome.get("status", "SUCCESS" if outcome.get("success") else "ERROR"))
        self.processed_count += 1
        return outcome


# Global Actor Reference management
# noinspection DuplicatedCode
_embedding_actor_ref: Optional[pykka.ActorRef] = None


def get_embedding_actor() -> pykka.ActorRef | None:
    """Get or start the singleton EmbeddingActor."""
    global _embedding_actor_ref
    if _embedding_actor_ref is None or not _embedding_actor_ref.is_alive():
        _embedding_actor_ref = EmbeddingActor.start()
    return _embedding_actor_ref


def stop_embedding_actor() -> None:
    """Stop the EmbeddingActor if running."""
    global _embedding_actor_ref
    if _embedding_actor_ref is not None and _embedding_actor_ref.is_alive():
        _embedding_actor_ref.stop()
        _embedding_actor_ref = None


def notify_embedding(
    resource: Any,
    *,
    group_context: Optional[Group] = None,
    group_id: Optional[str] = None,
    test_id: Optional[str] = None,
    method: EmbeddingMethod = EmbeddingMethod.DIAGNOSTIC_REPORT,
    tag: str = DEFAULT_EMBEDDING_TAG,
    acted_by: str = "embedding_actor",
) -> None:
    """Convenience fire-and-forget helper to enqueue a resource for vector embedding."""
    try:
        actor_ref = get_embedding_actor()
        if actor_ref:
            actor_ref.tell(
                EmbeddingMessage(
                    method=method,
                    resource=resource,
                    group_context=group_context,
                    group_id=group_id,
                    tag=tag,
                    test_id=test_id,
                    acted_by=acted_by,
                )
            )
    except Exception as exc:
        logger.error(
            "notify_embedding_failed",
            method=str(method),
            group_id=group_id,
            test_id=test_id,
            error=str(exc),
            exc_info=True,
        )
