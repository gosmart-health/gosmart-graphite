import base64
import re
from typing import Tuple, Optional

from fhir.resources.diagnosticreport import DiagnosticReport
from pydantic import BaseModel, Field

from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model import Group
from gosmart_graphite.model.llm_config import LLM_Config
from gosmart_graphite.service import GroupService, LLMConfigService

logger = get_logger("gosmart_gs.dianostic_report_service")


class ReportTexts(BaseModel):
    report: DiagnosticReport
    group_context: Optional[Group] = Field(default=None)
    hasText: bool = Field(default=False)
    llm_config: Optional[LLM_Config] = Field(default=None, description="LLM configuration for embedding generation")
    findings: str = Field(default="")
    conclusion: str = Field(default="")
    embedding_findings: list[float] = Field(default_factory=list)
    embedding_conclusion: list[float] = Field(default_factory=list)


class DiagnosticReportService:
    def __init__(self):
        self.group_service = GroupService()
        self.llm_config_service = LLMConfigService()
        pass

    @staticmethod
    def _detect_attachment_type(base64_data: str) -> Tuple[str, str]:
        """
        Detects the true MIME type of base64-encoded attachment data
        by inspecting leading magic bytes.
        If the attachment has straight text, it will return the MIME type and the decoded text.
        Otherwise, it will return the MIME type and an empty string.
        """
        if not base64_data:
            return "application/octet-stream", ""

        # Clean potential whitespace or data URI prefixes (e.g., data:application/pdf;base64,...)
        clean_b64 = re.sub(r"^data:[^;]+;base64,", "", base64_data.strip())

        # Grab the first 64 characters (yields up to 48 decoded bytes)
        sample_b64 = clean_b64[:64]

        # Pad if necessary for decoding
        padding = len(sample_b64) % 4
        if padding:
            sample_b64 += "=" * (4 - padding)

        try:
            header = base64.b64decode(sample_b64, validate=False)
        except Exception as why:
            logger.warning(f"Failed to decode base64 data: {why}")
            return "application/octet-stream", ""

        # Nothing decodable in the sample; treat as opaque binary
        if not header:
            return "application/octet-stream", ""

        # Strip UTF-8 BOM if present (\xef\xbb\xbf)
        if header.startswith(b"\xef\xbb\xbf"):
            header = header[3:]

        header_stripped = header.lstrip()

        # 1. PDF Check
        # PDF specification ISO 32000-1 allows up to 1024 bytes of header before %PDF-
        if b"%PDF-" in header[:32]:
            return "application/pdf", ""

        # 2. RTF Check
        if header_stripped.startswith(b"{\\rtf"):
            return "application/rtf", DiagnosticReportService._decode_full_text(clean_b64)

        # 3. HTML / XML Check
        header_lower = header_stripped.lower()
        if header_lower.startswith(b"<!doctype html") or header_lower.startswith(b"<html"):
            return "text/html", DiagnosticReportService._decode_full_text(clean_b64)
        if header_lower.startswith(b"<?xml"):
            return "application/xml", DiagnosticReportService._decode_full_text(clean_b64)

        # 4. Text Check (verify printable ASCII / UTF-8)
        try:
            decoded_sample = header.decode("utf-8")
            # Check for unprintable binary control characters
            if not any(ord(c) < 32 and c not in "\r\n\t\f\b" for c in decoded_sample):
                return "text/plain", DiagnosticReportService._decode_full_text(clean_b64)
        except UnicodeDecodeError as why:
            logger.warning(f"Failed to decode header as UTF-8: {why}")
            pass

        return "application/octet-stream", ""

    @staticmethod
    def _decode_full_text(clean_b64: str) -> str:
        """
        Decodes the entire base64 payload (already stripped of whitespace and
        any data URI prefix) into UTF-8 text.
        Returns an empty string if the payload cannot be decoded.
        """
        # Pad if necessary for decoding
        padding = len(clean_b64) % 4
        if padding:
            clean_b64 += "=" * (4 - padding)

        try:
            raw = base64.b64decode(clean_b64, validate=False)
            # utf-8-sig transparently strips a leading BOM if present
            return raw.decode("utf-8-sig")
        except Exception as why:
            logger.warning(f"Failed to decode full base64 payload as text: {why}")
            return ""

    @staticmethod
    def _extract_sections(report_text: ReportTexts, presentation_text: str) -> ReportTexts:
        """
        Extracts findings and conclusion sections from a presentation text.

        The presentation text is scanned for section headings (e.g. "Findings",
        "OBSERVATIONS:", "Impression") which may appear in any order, may be
        plural, cased arbitrarily and may or may not carry punctuation. Each
        section body extends from its heading up to the next heading (or the
        end of the text).

        The conclusion from ``report.conclusion`` takes precedence over any
        conclusion block found in the text. If neither a heading nor a
        conclusion can be found, the entire presentation text is treated as
        the conclusion.
        """
        findings_heading = [r"Findings?", r"Observations?"]
        conclusion_heading = [r"Conclusions?", r"Summary", r"Impressions?"]

        # Prefer the structured conclusion from the report when available.
        conclusion = ""
        if report_text.report.conclusion:
            conclusion = str(report_text.report.conclusion).strip()

        text = presentation_text or ""
        findings = ""
        matched_any_heading = False

        if text.strip():
            # Headings start on their own line and are either followed by a
            # colon (with optional content on the same line) or stand alone
            # on the line. This avoids mistaking prose such as "Findings were
            # unremarkable." for a section heading.
            heading_re = re.compile(
                r"(?im)^[ \t]*(?P<kind>"
                + "|".join(findings_heading + conclusion_heading)
                + r")\b[ \t]*(?::[ \t]*|[ \t]*$)"
            )
            # noinspection RegExpUnnecessaryNonCapturingGroup
            findings_kind_re = re.compile(r"(?i)^(?:" + "|".join(findings_heading) + r")$")

            findings_parts: list[str] = []
            conclusion_parts: list[str] = []

            matches = list(heading_re.finditer(text))
            matched_any_heading = bool(matches)
            for index, match in enumerate(matches):
                # Section body runs until the next heading or end of text.
                body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                body = text[match.end() : body_end].strip()
                kind = match.group("kind")
                if findings_kind_re.match(kind):
                    findings_parts.append(body)
                else:
                    conclusion_parts.append(body)

            findings = "\n".join(part for part in findings_parts if part).strip()
            if not conclusion:
                conclusion = "\n".join(part for part in conclusion_parts if part).strip()

        # No recognizable sections and no conclusion: treat the entire
        # presentation text as the conclusion.
        if not matched_any_heading and not conclusion:
            conclusion = text.strip()


        return ReportTexts(
            report=report_text.report,
            group_context=report_text.group_context,
            llm_config=report_text.llm_config,
            hasText=bool(text.strip() or conclusion),
            findings=findings,
            conclusion=conclusion,
            embedding_findings=[],
            embedding_conclusion=[],
        )

    def config_ensure(self, report_texts: ReportTexts) -> ReportTexts:
        if not report_texts.llm_config:
            report_texts.llm_config = LLM_Config()
        else:
            # TODO: Need to find the Group context first then traverse up
            # TODO: until we find the Group that has the LLM Config
            if not report_texts.group_context:
                # If Group context is not present we need to traverse back
                # from the DiagnosticReport to the Group that has the
                # LLM Config
                report_texts.group_context = Group()
        return report_texts

    def parse_diagnostic_report(self,
                                diagnostic_report: DiagnosticReport,
                                group_context: Optional[Group] = None,
                                llm_config: Optional[LLM_Config] = None,
                                ) -> ReportTexts:
        """
        Parses a DiagnosticReport object into a ReportTexts object.
        """
        result = ReportTexts(report=diagnostic_report, group_context=group_context,
                             llm_config=llm_config)
        # Find if presentedForm element is present, if present iterate until
        # we hit a parsable text
        for presented_form in diagnostic_report.presentedForm or []:
            # We do not rely on the encoding we will self determine
            # the content. fhir.resources decodes base64Binary into raw
            # bytes; re-encode to keep the base64-string contract of
            # _detect_attachment_type.
            raw_data = presented_form.data
            if isinstance(raw_data, (bytes, bytearray)):
                raw_data = base64.b64encode(raw_data).decode("ascii")
            content_type, text = self._detect_attachment_type(raw_data or "")
            if content_type == "text/plain" and text:
                return self._extract_sections(result, text)
            else:
                logger.warning(
                    f"Failed to decode base64 payload as text: {content_type} DiagnosticReport/{diagnostic_report.id}"
                )
        # No parsable presentation text; sections are still derived from the
        # structured report elements (e.g. conclusion) when available.
        return self._extract_sections(result, "")

    def compute_embeddings(self, report_texts: ReportTexts) -> ReportTexts:
        pass

    def graph_register(self, report_texts: ReportTexts) -> None:
        pass
