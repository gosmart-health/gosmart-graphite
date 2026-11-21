"""Unit tests for DiagnosticReportService.

Covers ``_detect_attachment_type``, ``_extract_sections`` and
``parse_diagnostic_report``. Self-contained tests suitable for CI
without external services.
"""

import base64
from typing import Final, TypedDict

import pytest
from fhir.resources.diagnosticreport import DiagnosticReport

from gosmart_graphite.model import Group
from gosmart_graphite.model.llm_config import LLM_Config
from gosmart_graphite.service.diagnostic_report_service import (
    DiagnosticReportService,
    ReportTexts,
)

CONCLUSION_TEXT: Final[str] = "Structured conclusion."
CT_ABDOMEN_CODE: Final[dict[str, str]] = {"text": "CT ABDOMEN"}


class _PresentedFormEntry(TypedDict):
    """Shape of a DiagnosticReport presentedForm attachment dict used in tests."""

    contentType: str
    data: str | bytes


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.fixture
def service() -> DiagnosticReportService:
    """DiagnosticReportService instance used by parse_diagnostic_report tests."""
    return DiagnosticReportService()


def _report_texts(conclusion: str | None = None) -> ReportTexts:
    data: dict[str, object] = {"status": "final", "code": CT_ABDOMEN_CODE}
    if conclusion is not None:
        data["conclusion"] = conclusion
    return ReportTexts(report=DiagnosticReport(**data))


def _diagnostic_report(
    *,
    report_id: str = "test-report",
    conclusion: str | None = None,
    presented_form: list[_PresentedFormEntry] | None = None,
) -> DiagnosticReport:
    data: dict[str, object] = {"id": report_id, "status": "final", "code": CT_ABDOMEN_CODE}
    if conclusion is not None:
        data["conclusion"] = conclusion
    if presented_form is not None:
        data["presentedForm"] = presented_form
    return DiagnosticReport(**data)


_DETECTION_CASES: Final[list[tuple[str, str, str]]] = [
    # (base64 payload, expected MIME type, expected decoded text)
    (
        _b64(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"),
        "application/pdf",
        "",
    ),
    (
        _b64(r"{\rtf1\ansi\deff0 Hello RTF document with some content.}".encode("utf-8")),
        "application/rtf",
        r"{\rtf1\ansi\deff0 Hello RTF document with some content.}",
    ),
    (
        "data:application/rtf;base64," + _b64(r"{\rtf1\ansi\deff0 RTF behind a data URI prefix.}".encode("utf-8")),
        "application/rtf",
        r"{\rtf1\ansi\deff0 RTF behind a data URI prefix.}",
    ),
    (
        _b64("<html><head><title>T</title></head><body>Body</body></html>".encode("utf-8")),
        "text/html",
        "<html><head><title>T</title></head><body>Body</body></html>",
    ),
    (
        _b64('<?xml version="1.0" encoding="UTF-8"?>\n<report><findings>OK</findings></report>\n'.encode("utf-8")),
        "application/xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n<report><findings>OK</findings></report>\n',
    ),
    (
        _b64(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        ),
        "application/octet-stream",
        "",
    ),
    (
        _b64(b"\x00\x01\x02\x03binary\x04data\x05"),
        "application/octet-stream",
        "",
    ),
    (
        "====!!!!",
        "application/octet-stream",
        "",
    ),
    (
        _b64(b"\xff\xfe\x00binary\x80\x81\xff text"),
        "application/octet-stream",
        "",
    ),
]


@pytest.mark.parametrize(
    ("payload", "expected_mime", "expected_text"),
    _DETECTION_CASES,
    ids=[
        "pdf",
        "rtf",
        "rtf-data-uri-prefix",
        "html-lowercase-tag",
        "xml",
        "png-binary",
        "control-characters",
        "invalid-base64",
        "invalid-utf-8",
    ],
)
def test_detect_attachment_type(payload: str, expected_mime: str, expected_text: str) -> None:
    mime, text = DiagnosticReportService._detect_attachment_type(payload)
    assert (mime, text) == (expected_mime, expected_text)


def test_empty_input_returns_octet_stream_tuple() -> None:
    result = DiagnosticReportService._detect_attachment_type("")
    assert result == ("application/octet-stream", "")


def test_html_doctype_with_data_uri_prefix_returns_full_text() -> None:
    html_text = "<!DOCTYPE html>\n<html><body><p>Report</p></body></html>\n"
    payload = "data:text/html;base64," + _b64(html_text.encode("utf-8"))
    mime, text = DiagnosticReportService._detect_attachment_type(payload)
    assert mime == "text/html"
    assert text == html_text


def test_plain_text_longer_than_sample_returns_full_text() -> None:
    # Longer than the 48-byte sniffing sample to prove the FULL payload is decoded.
    plain_text = "Patient presents with normal findings. " * 10
    assert len(plain_text.encode("utf-8")) > 48
    mime, text = DiagnosticReportService._detect_attachment_type(_b64(plain_text.encode("utf-8")))
    assert mime == "text/plain"
    assert text == plain_text


def test_plain_text_with_utf8_bom_strips_bom() -> None:
    plain_text = "BOM prefixed report text with unicode: caf\u00e9 \u65e5\u672c\u8a9e"
    raw = b"\xef\xbb\xbf" + plain_text.encode("utf-8")
    mime, text = DiagnosticReportService._detect_attachment_type(_b64(raw))
    assert mime == "text/plain"
    assert text == plain_text


def test_unpadded_base64_is_handled() -> None:
    # b64encode always pads; strip padding to simulate unpadded input.
    plain_text = "Unpadded base64 payload that still decodes correctly."
    unpadded = _b64(plain_text.encode("utf-8")).rstrip("=")
    mime, text = DiagnosticReportService._detect_attachment_type(unpadded)
    assert mime == "text/plain"
    assert text == plain_text


def test_leading_whitespace_before_text_still_detected() -> None:
    plain_text = "   \r\n  Indented report text after leading whitespace.\n"
    mime, text = DiagnosticReportService._detect_attachment_type(_b64(plain_text.encode("utf-8")))
    assert mime == "text/plain"
    assert text == plain_text


# ---------------------------------------------------------------------------
# _extract_sections
# ---------------------------------------------------------------------------


def test_extract_findings_then_conclusion_order() -> None:
    text = "Findings: The liver is normal in size.\nNo masses.\nImpression: No acute findings."
    result = DiagnosticReportService._extract_sections(_report_texts(), text)
    assert isinstance(result, ReportTexts)
    assert result.hasText is True
    assert result.findings == "The liver is normal in size.\nNo masses."
    assert result.conclusion == "No acute findings."
    assert result.embedding_findings == []
    assert result.embedding_conclusion == []


def test_extract_conclusion_before_findings_order() -> None:
    text = "CONCLUSION\nNo acute cardiopulmonary process.\n\nFINDINGS\nThe heart size is normal. The lungs are clear."
    result = DiagnosticReportService._extract_sections(_report_texts(), text)
    assert result.conclusion == "No acute cardiopulmonary process."
    assert result.findings == "The heart size is normal. The lungs are clear."


def test_extract_observations_heading_plural_and_punctuation_variants() -> None:
    text = "observations\n- Mild degenerative changes\nSUMMARY: Degenerative spine changes."
    result = DiagnosticReportService._extract_sections(_report_texts(), text)
    assert result.findings == "- Mild degenerative changes"
    assert result.conclusion == "Degenerative spine changes."


def test_extract_heading_without_colon_and_mixed_case() -> None:
    text = "finding\nLiver normal\nimpression\nNormal study"
    result = DiagnosticReportService._extract_sections(_report_texts(), text)
    assert result.findings == "Liver normal"
    assert result.conclusion == "Normal study"


def test_extract_multiple_findings_and_conclusion_headings_are_joined() -> None:
    text = "FINDINGS: First part.\nFINDINGS: Second part.\nIMPRESSION: Conclusion one.\nIMPRESSION: Conclusion two."
    result = DiagnosticReportService._extract_sections(_report_texts(), text)
    assert result.findings == "First part.\nSecond part."
    assert result.conclusion == "Conclusion one.\nConclusion two."


def test_report_conclusion_takes_precedence_over_text_block() -> None:
    text = "Findings: Liver normal.\nImpression: Text-based impression."
    result = DiagnosticReportService._extract_sections(_report_texts(CONCLUSION_TEXT), text)
    assert result.findings == "Liver normal."
    assert result.conclusion == CONCLUSION_TEXT


def test_report_conclusion_used_when_text_has_no_conclusion_heading() -> None:
    text = "Findings: Liver normal in size."
    result = DiagnosticReportService._extract_sections(_report_texts(CONCLUSION_TEXT), text)
    assert result.findings == "Liver normal in size."
    assert result.conclusion == CONCLUSION_TEXT


def test_no_headings_and_no_conclusion_treats_entire_text_as_conclusion() -> None:
    text = "Free form report text without any section headings at all."
    result = DiagnosticReportService._extract_sections(_report_texts(), text)
    assert result.findings == ""
    assert result.conclusion == text
    assert result.hasText is True


def test_no_headings_but_report_conclusion_present_keeps_findings_empty() -> None:
    text = "Free form report text without headings."
    result = DiagnosticReportService._extract_sections(_report_texts(CONCLUSION_TEXT), text)
    assert result.findings == ""
    assert result.conclusion == CONCLUSION_TEXT


def test_empty_text_and_no_conclusion_yields_empty_sections() -> None:
    result = DiagnosticReportService._extract_sections(_report_texts(), "")
    assert result.hasText is False
    assert result.findings == ""
    assert result.conclusion == ""


def test_empty_text_with_report_conclusion() -> None:
    result = DiagnosticReportService._extract_sections(_report_texts(CONCLUSION_TEXT), "")
    assert result.hasText is True
    assert result.findings == ""
    assert result.conclusion == CONCLUSION_TEXT


def test_preamble_before_first_heading_is_ignored() -> None:
    text = "RADIOLOGY REPORT\nExam: CT Abdomen\nFindings: Liver normal.\nImpression: Normal."
    result = DiagnosticReportService._extract_sections(_report_texts(), text)
    assert result.findings == "Liver normal."
    assert result.conclusion == "Normal."


def test_prose_lines_are_not_mistaken_for_headings() -> None:
    # "Findings were unremarkable" inside a sentence must not start a section.
    text = "Findings were unremarkable today.\nImpression: Normal study."
    result = DiagnosticReportService._extract_sections(_report_texts(), text)
    assert result.findings == ""
    assert result.conclusion == "Normal study."


# ---------------------------------------------------------------------------
# parse_diagnostic_report
# ---------------------------------------------------------------------------


def test_parse_diagnostic_report_plain_text_presented_form(service: DiagnosticReportService) -> None:
    text = "Findings: Liver normal.\nImpression: No acute findings."
    report = _diagnostic_report(
        presented_form=[_PresentedFormEntry(contentType="text/plain", data=_b64(text.encode("utf-8")))]
    )
    result = service.parse_diagnostic_report(report)
    assert result.hasText is True
    assert result.findings == "Liver normal."
    assert result.conclusion == "No acute findings."


def test_parse_diagnostic_report_presented_form_data_as_raw_bytes(
    service: DiagnosticReportService,
) -> None:
    # fhir.resources decodes base64Binary `data` into raw bytes; the service
    # re-encodes before sniffing, so bytes input must behave like str input.
    text = "Findings: Liver normal.\nImpression: No acute findings."
    report = _diagnostic_report(
        presented_form=[_PresentedFormEntry(contentType="text/plain", data=_b64(text.encode("utf-8")).encode("ascii"))]
    )
    result = service.parse_diagnostic_report(report)
    assert result.hasText is True
    assert result.findings == "Liver normal."
    assert result.conclusion == "No acute findings."


def test_parse_diagnostic_report_skips_unparsable_then_uses_text_presented_form(
    service: DiagnosticReportService,
) -> None:
    text = "Findings: Liver normal.\nImpression: No acute findings."
    report = _diagnostic_report(
        presented_form=[
            _PresentedFormEntry(contentType="application/pdf", data=_b64(b"%PDF-1.4\n")),
            _PresentedFormEntry(contentType="text/plain", data=_b64(text.encode("utf-8"))),
        ]
    )
    result = service.parse_diagnostic_report(report)
    assert result.hasText is True
    assert result.findings == "Liver normal."
    assert result.conclusion == "No acute findings."


def test_parse_diagnostic_report_without_presented_form_uses_conclusion(
    service: DiagnosticReportService,
) -> None:
    report = _diagnostic_report(conclusion=CONCLUSION_TEXT)
    result = service.parse_diagnostic_report(report)
    assert result.hasText is True
    assert result.findings == ""
    assert result.conclusion == CONCLUSION_TEXT


def test_parse_diagnostic_report_unparsable_attachment_falls_back(
    service: DiagnosticReportService,
) -> None:
    report = _diagnostic_report(
        report_id="test-parse-unparsable",
        conclusion=CONCLUSION_TEXT,
        presented_form=[_PresentedFormEntry(contentType="application/pdf", data=_b64(b"%PDF-1.7\n"))],
    )
    result = service.parse_diagnostic_report(report)
    assert result.hasText is True
    assert result.findings == ""
    assert result.conclusion == CONCLUSION_TEXT


def test_parse_diagnostic_report_passes_group_context_and_llm_config_through(
    service: DiagnosticReportService,
) -> None:
    group_context = Group(name="Test Clinic", code="TST-01", test_id="test_parse_pass_through")
    llm_config = LLM_Config(
        tag="embedding",
        provider="ollama",
        model="nomic-embed-text:latest",
        test_id="test_parse_pass_through",
    )
    report = _diagnostic_report(conclusion=CONCLUSION_TEXT)
    result = service.parse_diagnostic_report(report, group_context=group_context, llm_config=llm_config)
    assert result.group_context is group_context
    assert result.llm_config is llm_config
    assert result.conclusion == CONCLUSION_TEXT


# ---------------------------------------------------------------------------
# config_ensure
# ---------------------------------------------------------------------------


def test_config_ensure_preserves_existing_group_and_llm_config() -> None:
    from unittest.mock import MagicMock

    from gosmart_graphite.model.result import STATUS_OK

    mock_group_svc = MagicMock()
    mock_llm_svc = MagicMock()
    svc = DiagnosticReportService(group_service=mock_group_svc, llm_config_service=mock_llm_svc)

    existing_group = Group(name="Existing Clinic", code="EXIST_01", test_id="t_cfg_existing")
    existing_llm = LLM_Config(
        tag="embedding-v1",
        provider="ollama",
        model="nomic-embed-text:latest",
        test_id="t_cfg_existing",
    )
    report_texts = ReportTexts(
        report=_diagnostic_report(report_id="dr-existing"),
        group_context=existing_group,
        llm_config=existing_llm,
    )

    res = svc.config_ensure(report_texts)
    assert res.status == STATUS_OK
    assert report_texts.group_context is existing_group
    assert report_texts.llm_config is existing_llm
    mock_group_svc.my_group_sync.assert_not_called()
    mock_llm_svc.resolve_config_for_group_sync.assert_not_called()


def test_config_ensure_resolves_group_from_diagnostic_report_then_llm_config() -> None:
    from unittest.mock import MagicMock

    from gosmart_graphite.model.fhir import DiagnosticReportNode
    from gosmart_graphite.model.result import STATUS_OK
    from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG

    resolved_group = Group(name="Half Moon Bay Clinic", code="HMB_01", test_id="t_cfg_dr")
    resolved_llm = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="ollama",
        model="nomic-embed-text:latest",
        url="http://localhost:11434",
        test_id="t_cfg_dr",
    )

    mock_group_svc = MagicMock()
    mock_group_svc.my_group_sync.return_value = resolved_group
    mock_llm_svc = MagicMock()
    mock_llm_svc.resolve_config_for_group_sync.return_value = resolved_llm
    svc = DiagnosticReportService(group_service=mock_group_svc, llm_config_service=mock_llm_svc)

    report = _diagnostic_report(report_id="dr-1001")
    report_texts = ReportTexts(report=report)

    res = svc.config_ensure(report_texts, test_id="t_cfg_dr")
    assert res.status == STATUS_OK
    assert report_texts.group_context == resolved_group
    assert report_texts.llm_config == resolved_llm

    expected_dr_node_id = DiagnosticReportNode(resource_id="dr-1001", resource="{}", test_id="t_cfg_dr").id
    mock_group_svc.my_group_sync.assert_called_once_with(
        expected_dr_node_id,
        return_lost_and_found_group=False,
    )
    mock_llm_svc.resolve_config_for_group_sync.assert_called_once_with(
        tag=DEFAULT_EMBEDDING_TAG,
        group_id=resolved_group.id,
        test_id="t_cfg_dr",
    )


def test_config_ensure_falls_back_to_encounter_reference_for_group() -> None:
    from unittest.mock import MagicMock

    from gosmart_graphite.model.fhir import DiagnosticReportNode, EncounterNode
    from gosmart_graphite.model.result import STATUS_OK
    from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG

    resolved_group = Group(name="Encounter Clinic", code="ENC_CLINIC", test_id="t_cfg_enc")
    resolved_llm = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="ollama",
        model="nomic-embed-text:latest",
        test_id="t_cfg_enc",
    )

    expected_dr_node_id = DiagnosticReportNode(resource_id="dr-enc-1", resource="{}", test_id="t_cfg_enc").id
    expected_enc_node_id = EncounterNode(resource_id="enc-999", resource="{}", test_id="t_cfg_enc").id

    def _fake_my_group(node_id: str, return_lost_and_found_group: bool = True):
        assert return_lost_and_found_group is False
        if node_id == expected_enc_node_id:
            return resolved_group
        return None

    mock_group_svc = MagicMock()
    mock_group_svc.my_group_sync.side_effect = _fake_my_group
    mock_llm_svc = MagicMock()
    mock_llm_svc.resolve_config_for_group_sync.return_value = resolved_llm
    svc = DiagnosticReportService(group_service=mock_group_svc, llm_config_service=mock_llm_svc)

    report = DiagnosticReport(
        id="dr-enc-1",
        status="final",
        code=CT_ABDOMEN_CODE,
        encounter={"reference": "Encounter/enc-999"},
    )
    report_texts = ReportTexts(report=report)

    res = svc.config_ensure(report_texts, test_id="t_cfg_enc")
    assert res.status == STATUS_OK
    assert report_texts.group_context == resolved_group
    assert report_texts.llm_config == resolved_llm
    assert [c.args[0] for c in mock_group_svc.my_group_sync.call_args_list] == [
        expected_dr_node_id,
        expected_enc_node_id,
    ]


def test_config_ensure_falls_back_to_lost_and_found_when_group_unresolved() -> None:
    from unittest.mock import MagicMock, patch

    from gosmart_graphite.model.result import STATUS_OK
    from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG

    lost_and_found = Group(name="LostAndFound", code="LOST_AND_FOUND", group_type="SYSTEM")
    default_llm = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="ollama",
        model="nomic-embed-text:latest",
    )

    mock_group_svc = MagicMock()
    mock_group_svc.my_group_sync.return_value = None
    mock_group_svc.group_or_lost_and_found.return_value = lost_and_found
    mock_llm_svc = MagicMock()
    mock_llm_svc.resolve_config_for_group_sync.return_value = default_llm
    svc = DiagnosticReportService(group_service=mock_group_svc, llm_config_service=mock_llm_svc)

    report_texts = ReportTexts(report=_diagnostic_report(report_id="dr-orphan"))
    with patch("gosmart_graphite.service.diagnostic_report_service.logger") as mock_logger:
        res = svc.config_ensure(report_texts)

    assert res.status == STATUS_OK
    assert report_texts.group_context == lost_and_found
    assert report_texts.llm_config == default_llm
    mock_group_svc.group_or_lost_and_found.assert_called_once_with(None)
    mock_llm_svc.resolve_config_for_group_sync.assert_called_once_with(
        tag=DEFAULT_EMBEDDING_TAG,
        group_id=lost_and_found.id,
        test_id=None,
    )
    mock_logger.warning.assert_called_once()
    assert mock_logger.warning.call_args.args[0] == "diagnostic_report_group_not_found_using_lost_and_found"


def test_config_ensure_returns_bad_data_when_llm_config_not_found() -> None:
    from unittest.mock import MagicMock

    from gosmart_graphite.model.result import STATUS_BAD_DATA

    resolved_group = Group(name="No LLM Clinic", code="NO_LLM_01")
    mock_group_svc = MagicMock()
    mock_group_svc.my_group_sync.return_value = resolved_group
    mock_llm_svc = MagicMock()
    mock_llm_svc.resolve_config_for_group_sync.return_value = None
    svc = DiagnosticReportService(group_service=mock_group_svc, llm_config_service=mock_llm_svc)

    report_texts = ReportTexts(report=_diagnostic_report(report_id="dr-no-llm"))
    res = svc.config_ensure(report_texts)

    assert res.status == STATUS_BAD_DATA
    assert "No active LLM_Config found" in res.message
    assert report_texts.group_context == resolved_group
    assert report_texts.llm_config is None


def test_config_ensure_returns_internal_error_on_llm_config_exception() -> None:
    from unittest.mock import MagicMock

    from gosmart_graphite.model.result import STATUS_INTERNAL_ERROR

    resolved_group = Group(name="Ambiguous Clinic", code="AMB_01")
    mock_group_svc = MagicMock()
    mock_group_svc.my_group_sync.return_value = resolved_group
    mock_llm_svc = MagicMock()
    mock_llm_svc.resolve_config_for_group_sync.side_effect = RuntimeError("Ambiguous LLM_Config")
    svc = DiagnosticReportService(group_service=mock_group_svc, llm_config_service=mock_llm_svc)

    report_texts = ReportTexts(report=_diagnostic_report(report_id="dr-amb-llm"))
    res = svc.config_ensure(report_texts)

    assert res.status == STATUS_INTERNAL_ERROR
    assert "Ambiguous LLM_Config" in res.message


@pytest.mark.anyio
async def test_compute_embeddings_and_graph_register_propagate_config_ensure_failure() -> None:
    from unittest.mock import MagicMock

    from gosmart_graphite.model.result import STATUS_BAD_DATA

    resolved_group = Group(name="Propagation Clinic", code="PROP_01")
    mock_group_svc = MagicMock()
    mock_group_svc.my_group_sync.return_value = resolved_group
    mock_llm_svc = MagicMock()
    mock_llm_svc.resolve_config_for_group_sync.return_value = None
    svc = DiagnosticReportService(group_service=mock_group_svc, llm_config_service=mock_llm_svc)

    rt_embed = ReportTexts(report=_diagnostic_report(report_id="dr-prop-1"))
    embed_res = await svc.compute_embeddings(rt_embed)
    assert embed_res.status == STATUS_BAD_DATA
    assert "No active LLM_Config found" in embed_res.message

    rt_reg = ReportTexts(report=_diagnostic_report(report_id="dr-prop-2"))
    reg_res = svc.graph_register(rt_reg)
    assert reg_res.status == STATUS_BAD_DATA
    assert "No active LLM_Config found" in reg_res.message
