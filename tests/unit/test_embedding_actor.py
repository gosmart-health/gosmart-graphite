"""Unit tests for EmbeddingActor, EmbeddingMessage, EmbeddingMethod, and notify_embedding."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from pydantic import ValidationError

from gosmart_graphite.actor.embedding_actor import (
    EmbeddingActor,
    notify_embedding,
)
from gosmart_graphite.model.actor_message import (
    DEFAULT_EMBEDDING_TAG,
    EmbeddingMessage,
    EmbeddingMethod,
)
from gosmart_graphite.model.auth import Group
from gosmart_graphite.model.embedding import (
    DiagnosticReportFieldTag,
    EmbeddingNode,
)
from gosmart_graphite.model.result import (
    STATUS_BAD_DATA,
    STATUS_INTERNAL_ERROR,
    STATUS_OK,
    Result,
)
from gosmart_graphite.service.diagnostic_report_service import ReportTexts


def _sample_r4b_report(report_id: str = "dr-emb-unit-1") -> DiagnosticReport:
    return DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": report_id,
            "status": "final",
            "code": {"text": "CT CHEST"},
            "encounter": {"reference": "Encounter/enc-unit-1"},
            "conclusion": "No acute cardiopulmonary abnormality.",
        }
    )


def _sample_group() -> Group:
    return Group(
        id="grp-clinic-unit-1",
        name="Unit Clinic",
        code="UNIT_CLINIC",
        group_type="FACILITY",
        is_active=True,
    )


def test_embedding_message_validation_and_defaults():
    report = _sample_r4b_report()
    msg = EmbeddingMessage(resource=report)
    assert msg.type == "EMBED_RESOURCE"
    assert msg.method == EmbeddingMethod.DIAGNOSTIC_REPORT
    assert msg.resource is report
    assert msg.group_context is None
    assert msg.group_id is None
    assert msg.tag == DEFAULT_EMBEDDING_TAG
    assert msg.test_id is None
    assert msg.acted_by == "embedding_actor"

    # Providing group_context syncs group_id automatically when omitted
    grp = _sample_group()
    msg_with_ctx = EmbeddingMessage(
        resource=report,
        group_context=grp,
        group_id="   ",
        tag="  ",
        test_id="  test-run-1  ",
        acted_by="   ",
    )
    assert msg_with_ctx.group_context == grp
    assert msg_with_ctx.group_id == grp.id
    assert msg_with_ctx.tag == DEFAULT_EMBEDDING_TAG
    assert msg_with_ctx.test_id == "test-run-1"
    assert msg_with_ctx.acted_by == "embedding_actor"

    # Explicit group_id is kept when supplied alongside group_context
    msg_explicit_gid = EmbeddingMessage(
        resource=report,
        group_context=grp,
        group_id="grp-explicit",
    )
    assert msg_explicit_gid.group_id == "grp-explicit"

    # Validation failures
    with pytest.raises(ValidationError):
        EmbeddingMessage(resource=None)

    with pytest.raises(ValidationError):
        EmbeddingMessage(resource=report, method="unsupported_method")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        EmbeddingMessage(resource=report, tag=123)  # type: ignore[arg-type]


def test_embedding_actor_control_and_validation_messages():
    actor = EmbeddingActor.start()
    try:
        assert actor.is_alive()

        assert actor.ask({"type": "PING"}, timeout=2.0) == {
            "status": "PONG",
            "processed_count": 0,
        }
        assert actor.ask({"type": "STATUS"}, timeout=2.0) == {
            "processed_count": 0,
            "last_status": "INITIALIZED",
        }

        invalid_dict = actor.ask({"type": "EMBED_RESOURCE"}, timeout=2.0)
        assert invalid_dict["success"] is False
        assert "Invalid EmbeddingMessage" in invalid_dict["error"]

        unsupported = actor.ask("plain-string-message", timeout=2.0)
        assert unsupported["success"] is False
        assert "Unsupported message type" in unsupported["error"]
    finally:
        actor.stop()
        assert not actor.is_alive()


def test_embedding_actor_diagnostic_report_pipeline_with_group_context():
    report = _sample_r4b_report("dr-ctx-1")
    grp = _sample_group()
    rt = ReportTexts(
        report=report,
        group_context=grp,
        hasText=True,
        findings="Clear lungs.",
        conclusion="Normal chest.",
        embedding_findings=[1.0, 0.0],
        embedding_conclusion=[0.0, 1.0],
    )
    emb_nodes = [
        EmbeddingNode(
            modelTag=DEFAULT_EMBEDDING_TAG,
            size=2,
            vector=[1.0, 0.0],
            targetFieldTag=DiagnosticReportFieldTag.FINDINGS,
            target_node_id="dr-node-1",
            test_id="t_emb_ctx",
        ),
        EmbeddingNode(
            modelTag=DEFAULT_EMBEDDING_TAG,
            size=2,
            vector=[0.0, 1.0],
            targetFieldTag=DiagnosticReportFieldTag.CONCLUSION,
            target_node_id="dr-node-1",
            test_id="t_emb_ctx",
        ),
    ]

    mock_svc = MagicMock()
    mock_svc.group_service = MagicMock()
    mock_svc.parse_diagnostic_report = MagicMock(return_value=rt)
    mock_svc.compute_embeddings = AsyncMock(return_value=Result(status=STATUS_OK, message="OK", value=rt))
    mock_svc.graph_register = MagicMock(
        return_value=Result(status=STATUS_OK, message="Registered 2 embeddings", value=emb_nodes)
    )

    actor = EmbeddingActor.start(diagnostic_report_service=mock_svc)
    try:
        outcome = actor.ask(
            EmbeddingMessage(
                method=EmbeddingMethod.DIAGNOSTIC_REPORT,
                resource=report,
                group_context=grp,
                test_id="t_emb_ctx",
                acted_by="tester",
            ),
            timeout=3.0,
        )

        assert outcome["success"] is True
        assert outcome["method"] == str(EmbeddingMethod.DIAGNOSTIC_REPORT)
        assert outcome["report_id"] == "dr-ctx-1"
        assert outcome["status"] == "SUCCESS"
        assert outcome["status_code"] == STATUS_OK
        assert outcome["registered_count"] == 2
        assert outcome["embedding_ids"] == [emb_nodes[0].id, emb_nodes[1].id]

        mock_svc.group_service.resolve_target_group_sync.assert_not_called()
        mock_svc.parse_diagnostic_report.assert_called_once_with(report, group_context=grp)
        mock_svc.compute_embeddings.assert_awaited_once_with(
            rt,
            tag=DEFAULT_EMBEDDING_TAG,
            test_id="t_emb_ctx",
        )
        mock_svc.graph_register.assert_called_once_with(
            rt,
            tag=DEFAULT_EMBEDDING_TAG,
            test_id="t_emb_ctx",
            acted_by="tester",
        )

        status = actor.ask({"type": "STATUS"}, timeout=2.0)
        assert status == {"processed_count": 1, "last_status": "SUCCESS"}
    finally:
        actor.stop()


def test_embedding_actor_resolves_group_id_and_coerces_dict_report():
    report_dict = _sample_r4b_report("dr-dict-1").model_dump(by_alias=True, exclude_none=True)
    grp = _sample_group()
    captured_reports: list[DiagnosticReport] = []
    captured_groups: list[Group | None] = []

    def _fake_parse(report: DiagnosticReport, group_context: Group | None = None) -> ReportTexts:
        captured_reports.append(report)
        captured_groups.append(group_context)
        return ReportTexts(report=report, group_context=group_context, hasText=False)

    mock_svc = MagicMock()
    mock_svc.group_service = MagicMock()
    mock_svc.group_service.resolve_target_group_sync = MagicMock(
        return_value={
            "id": grp.id,
            "name": grp.name,
            "code": grp.code,
            "group_type": grp.group_type,
            "is_active": True,
        }
    )
    mock_svc.parse_diagnostic_report = MagicMock(side_effect=_fake_parse)
    mock_svc.compute_embeddings = AsyncMock(return_value=Result(status=STATUS_OK, message="OK", value=None))
    mock_svc.graph_register = MagicMock(
        return_value=Result(status=STATUS_OK, message="No vectors to register", value=[])
    )

    actor = EmbeddingActor.start(diagnostic_report_service=mock_svc)
    try:
        outcome = actor.ask(
            {
                "type": "EMBED_RESOURCE",
                "method": "diagnostic_report",
                "resource": report_dict,
                "group_id": grp.id,
                "test_id": "t_dict_gid",
            },
            timeout=3.0,
        )

        assert outcome["success"] is True
        assert outcome["report_id"] == "dr-dict-1"
        assert outcome["registered_count"] == 0
        mock_svc.group_service.resolve_target_group_sync.assert_called_once_with(
            identifier=grp.id,
            test_id="t_dict_gid",
        )
        assert len(captured_reports) == 1
        assert isinstance(captured_reports[0], DiagnosticReport)
        assert captured_groups[0] is not None
        assert captured_groups[0].id == grp.id
    finally:
        actor.stop()


def test_embedding_actor_group_id_resolve_failure_falls_back_to_none():
    report = _sample_r4b_report("dr-grp-fallback")
    rt = ReportTexts(report=report, hasText=False)

    mock_svc = MagicMock()
    mock_svc.group_service = MagicMock()
    mock_svc.group_service.resolve_target_group_sync = MagicMock(side_effect=RuntimeError("Neo4j lookup error"))
    mock_svc.parse_diagnostic_report = MagicMock(return_value=rt)
    mock_svc.compute_embeddings = AsyncMock(return_value=Result(status=STATUS_OK, message="OK", value=rt))
    mock_svc.graph_register = MagicMock(return_value=Result(status=STATUS_OK, message="OK", value=[]))

    actor = EmbeddingActor.start(diagnostic_report_service=mock_svc)
    try:
        outcome = actor.ask(
            EmbeddingMessage(resource=report, group_id="grp-missing", test_id="t_fallback"),
            timeout=3.0,
        )
        assert outcome["success"] is True
        mock_svc.parse_diagnostic_report.assert_called_once_with(report, group_context=None)
    finally:
        actor.stop()


def test_embedding_actor_compute_failure_skips_graph_register():
    report = _sample_r4b_report("dr-compute-fail")
    rt = ReportTexts(report=report, hasText=True, conclusion="Some conclusion")

    mock_svc = MagicMock()
    mock_svc.group_service = MagicMock()
    mock_svc.parse_diagnostic_report = MagicMock(return_value=rt)
    mock_svc.compute_embeddings = AsyncMock(return_value=Result(status=STATUS_BAD_DATA, message="No active LLM_Config"))
    mock_svc.graph_register = MagicMock()

    actor = EmbeddingActor.start(diagnostic_report_service=mock_svc)
    try:
        outcome = actor.ask(EmbeddingMessage(resource=report, test_id="t_comp_fail"), timeout=3.0)
        assert outcome["success"] is False
        assert outcome["status"] == f"COMPUTE_FAILED:{STATUS_BAD_DATA}"
        assert outcome["status_code"] == STATUS_BAD_DATA
        assert "No active LLM_Config" in outcome["error"]
        mock_svc.graph_register.assert_not_called()
    finally:
        actor.stop()


def test_embedding_actor_register_failure_returns_error():
    report = _sample_r4b_report("dr-reg-fail")
    rt = ReportTexts(report=report, hasText=True, conclusion="Some conclusion", embedding_conclusion=[1.0, 0.0])

    mock_svc = MagicMock()
    mock_svc.group_service = MagicMock()
    mock_svc.parse_diagnostic_report = MagicMock(return_value=rt)
    mock_svc.compute_embeddings = AsyncMock(return_value=Result(status=STATUS_OK, message="OK", value=rt))
    mock_svc.graph_register = MagicMock(return_value=Result(status=STATUS_INTERNAL_ERROR, message="Neo4j write failed"))

    actor = EmbeddingActor.start(diagnostic_report_service=mock_svc)
    try:
        outcome = actor.ask(EmbeddingMessage(resource=report, test_id="t_reg_fail"), timeout=3.0)
        assert outcome["success"] is False
        assert outcome["status"] == f"REGISTER_FAILED:{STATUS_INTERNAL_ERROR}"
        assert outcome["status_code"] == STATUS_INTERNAL_ERROR
        assert "Neo4j write failed" in outcome["error"]
    finally:
        actor.stop()


def test_embedding_actor_handles_unexpected_exception_and_stays_alive():
    report = _sample_r4b_report("dr-boom")
    mock_svc = MagicMock()
    mock_svc.group_service = MagicMock()
    mock_svc.parse_diagnostic_report = MagicMock(side_effect=RuntimeError("unexpected parser crash"))

    actor = EmbeddingActor.start(diagnostic_report_service=mock_svc)
    try:
        outcome = actor.ask(EmbeddingMessage(resource=report), timeout=3.0)
        assert outcome["success"] is False
        assert "unexpected parser crash" in outcome["error"]

        # Actor remains healthy after recovering from handler exception
        ping = actor.ask({"type": "PING"}, timeout=2.0)
        assert ping == {"status": "PONG", "processed_count": 1}
        status = actor.ask({"type": "STATUS"}, timeout=2.0)
        assert status["processed_count"] == 1
        assert "ERROR: unexpected parser crash" == status["last_status"]
    finally:
        actor.stop()


def test_embedding_actor_custom_method_handler_dispatch():
    report = _sample_r4b_report("dr-custom")
    captured_msgs: list[EmbeddingMessage] = []

    def _custom_handler(msg: EmbeddingMessage) -> dict:
        captured_msgs.append(msg)
        return {"success": True, "status": "CUSTOM_OK", "method": str(msg.method)}

    actor = EmbeddingActor.start(
        method_handlers={EmbeddingMethod.DIAGNOSTIC_REPORT: _custom_handler},
    )
    try:
        outcome = actor.ask(
            EmbeddingMessage(
                method=EmbeddingMethod.DIAGNOSTIC_REPORT,
                resource=report,
                test_id="t_custom",
            ),
            timeout=2.0,
        )
        assert outcome == {"success": True, "status": "CUSTOM_OK", "method": "diagnostic_report"}
        assert len(captured_msgs) == 1
        assert captured_msgs[0].resource is report
        assert captured_msgs[0].test_id == "t_custom"
    finally:
        actor.stop()


def test_notify_embedding_sends_validated_message_and_handles_errors():
    report = _sample_r4b_report("dr-notify-1")
    grp = _sample_group()
    mock_actor = MagicMock()

    with patch("gosmart_graphite.actor.embedding_actor.get_embedding_actor", return_value=mock_actor):
        notify_embedding(
            report,
            group_context=grp,
            test_id="t_notify",
            method=EmbeddingMethod.DIAGNOSTIC_REPORT,
            tag="custom-emb-tag",
            acted_by="fhir_actor",
        )

    mock_actor.tell.assert_called_once()
    sent_msg = mock_actor.tell.call_args[0][0]
    assert isinstance(sent_msg, EmbeddingMessage)
    assert sent_msg.resource is report
    assert sent_msg.group_context == grp
    assert sent_msg.group_id == grp.id
    assert sent_msg.test_id == "t_notify"
    assert sent_msg.method == EmbeddingMethod.DIAGNOSTIC_REPORT
    assert sent_msg.tag == "custom-emb-tag"
    assert sent_msg.acted_by == "fhir_actor"

    # Exception during actor lookup is swallowed and logged
    with (
        patch(
            "gosmart_graphite.actor.embedding_actor.get_embedding_actor",
            side_effect=RuntimeError("actor start error"),
        ),
        patch("gosmart_graphite.actor.embedding_actor.logger") as mock_logger,
    ):
        notify_embedding(report, group_id="grp-1", test_id="t_err")
        mock_logger.error.assert_called_once()
        assert mock_logger.error.call_args.args[0] == "notify_embedding_failed"
