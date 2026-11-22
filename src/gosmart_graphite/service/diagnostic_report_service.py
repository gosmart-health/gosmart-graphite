import asyncio
import base64
import re
from typing import Any, LiteralString, Optional, Tuple

from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from pydantic import BaseModel, Field, ValidationError

from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.db.neo4j import Neo4jClient, neo4j_client
from gosmart_graphite.model import Group, Result
from gosmart_graphite.model.embedding import (
    DEFAULT_EMBEDDING_VECTOR_DIMENSIONS,
    DiagnosticReportFieldTag,
    Embedding,
    EmbedsRelation,
)
from gosmart_graphite.model.fhir import DiagnosticReportNode, EncounterNode
from gosmart_graphite.model.llm_config import LLM_Config
from gosmart_graphite.model.result import (
    STATUS_BAD_DATA,
    STATUS_INTERNAL_ERROR,
    STATUS_OK,
)
from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG, EmbeddingService
from gosmart_graphite.service.group_service import GroupService
from gosmart_graphite.service.llm_config_service import LLMConfigService

logger = get_logger("gosmart_gs.dianostic_report_service")

_UPSERT_REPORT_EMBEDDINGS_CYPHER: LiteralString = """
MATCH (dr:DiagnosticReport {id: $target_id})
WHERE dr.Action IS NULL OR dr.Action <> 'DELETE'
WITH dr
UNWIND $items AS item
MERGE (e:Embedding {id: item.emb.id})
ON CREATE SET
    e.modelTag = item.emb.modelTag,
    e.size = item.emb.size,
    e.vector = item.emb.vector,
    e.targetFieldTag = item.emb.targetFieldTag,
    e.test_id = item.emb.test_id,
    e.Action = 'CREATE',
    e.ActedAt = datetime({timezone: 'UTC'}),
    e.ActedBy = $acted_by,
    e.CreatedAt = datetime({timezone: 'UTC'}),
    e.ExpiresAt = CASE WHEN item.emb.ExpiresAt IS NOT NULL THEN datetime(item.emb.ExpiresAt) ELSE null END
ON MATCH SET
    e.modelTag = item.emb.modelTag,
    e.size = item.emb.size,
    e.vector = item.emb.vector,
    e.targetFieldTag = item.emb.targetFieldTag,
    e.test_id = coalesce(item.emb.test_id, e.test_id),
    e.Action = 'UPDATE',
    e.ActedAt = datetime({timezone: 'UTC'}),
    e.ActedBy = $acted_by,
    e.UpdatedAt = datetime({timezone: 'UTC'}),
    e.ExpiresAt = CASE WHEN item.emb.ExpiresAt IS NOT NULL THEN datetime(item.emb.ExpiresAt) ELSE e.ExpiresAt END
MERGE (e)-[r:EMBEDS {id: item.rel.id}]->(dr)
ON CREATE SET
    r.aspect = item.rel.aspect,
    r.test_id = item.rel.test_id,
    r.Action = 'CREATE',
    r.ActedAt = datetime({timezone: 'UTC'}),
    r.ActedBy = $acted_by,
    r.CreatedAt = datetime({timezone: 'UTC'}),
    r.ExpiresAt = CASE WHEN item.rel.ExpiresAt IS NOT NULL THEN datetime(item.rel.ExpiresAt) ELSE null END
ON MATCH SET
    r.aspect = item.rel.aspect,
    r.test_id = coalesce(item.rel.test_id, r.test_id),
    r.Action = 'UPDATE',
    r.ActedAt = datetime({timezone: 'UTC'}),
    r.ActedBy = $acted_by,
    r.UpdatedAt = datetime({timezone: 'UTC'}),
    r.ExpiresAt = CASE WHEN item.rel.ExpiresAt IS NOT NULL THEN datetime(item.rel.ExpiresAt) ELSE r.ExpiresAt END
RETURN e.id AS embedding_id, r.id AS relation_id, e.Action AS action
"""


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
    def __init__(
        self,
        group_service: Optional[GroupService] = None,
        llm_config_service: Optional[LLMConfigService] = None,
        embedding_service: Optional[EmbeddingService] = None,
        client: Optional[Neo4jClient] = None,
    ) -> None:
        self.client = client or neo4j_client
        self.group_service = group_service or GroupService(client=self.client)
        self.llm_config_service = llm_config_service or LLMConfigService(client=self.client)
        self.embedding_service = embedding_service or EmbeddingService(llm_config_service=self.llm_config_service)

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

    @staticmethod
    def _extract_encounter_resource_id(report: DiagnosticReport) -> Optional[str]:
        """Extract the FHIR Encounter resource ID from DiagnosticReport.encounter, if present."""
        encounter_ref = getattr(report, "encounter", None)
        if encounter_ref is None:
            return None
        ref_str = getattr(encounter_ref, "reference", None)
        if isinstance(ref_str, str) and ref_str.strip():
            tail = ref_str.strip().rstrip("/").split("/")[-1]
            return tail if tail else None
        ref_id = getattr(encounter_ref, "id", None)
        if isinstance(ref_id, str) and ref_id.strip():
            return ref_id.strip()
        return None

    def _resolve_report_group(
        self,
        report: DiagnosticReport,
        test_id: Optional[str] = None,
    ) -> Group:
        """Resolve the nearest active Group for a DiagnosticReport, falling back to LostAndFound."""
        report_id = getattr(report, "id", None)
        clean_report_id = report_id.strip() if isinstance(report_id, str) else ""
        if clean_report_id:
            dr_node_id = DiagnosticReportNode(
                resource_id=clean_report_id,
                resource="{}",
                test_id=test_id,
            ).id
            resolved = self.group_service.my_group_sync(
                dr_node_id,
                return_lost_and_found_group=False,
            )
            if resolved is not None:
                return resolved

        enc_res_id = self._extract_encounter_resource_id(report)
        if enc_res_id:
            enc_node_id = EncounterNode(
                resource_id=enc_res_id,
                resource="{}",
                test_id=test_id,
            ).id
            resolved = self.group_service.my_group_sync(
                enc_node_id,
                return_lost_and_found_group=False,
            )
            if resolved is not None:
                return resolved

        fallback = self.group_service.group_or_lost_and_found(None)
        logger.warning(
            "diagnostic_report_group_not_found_using_lost_and_found",
            diagnostic_report_id=clean_report_id or None,
            encounter_id=enc_res_id,
            lost_and_found_group_id=fallback.id,
        )
        return fallback

    def config_ensure(
        self,
        report_texts: ReportTexts,
        *,
        tag: str = DEFAULT_EMBEDDING_TAG,
        test_id: Optional[str] = None,
    ) -> Result[ReportTexts]:
        """Ensure report_texts has both group_context and llm_config populated.

        Resolves ``group_context`` first (via the persisted ``DiagnosticReport`` node or
        its referenced ``Encounter`` node, falling back to ``LostAndFound``), then
        resolves ``llm_config`` for ``tag`` (default ``DEFAULT_EMBEDDING_TAG``) upward
        from ``group_context.id`` with ``'GoSmart Health'`` fallback.
        """

        effective_test_id = (
            test_id
            or (report_texts.group_context.test_id if report_texts.group_context else None)
            or (report_texts.llm_config.test_id if report_texts.llm_config else None)
        )

        if report_texts.group_context is None:
            try:
                report_texts.group_context = self._resolve_report_group(
                    report_texts.report,
                    test_id=effective_test_id,
                )
            except Exception as exc:
                logger.error(
                    "diagnostic_report_group_resolution_failed",
                    diagnostic_report_id=getattr(report_texts.report, "id", None),
                    error=str(exc),
                    exc_info=True,
                )
                return Result[ReportTexts](
                    status=STATUS_INTERNAL_ERROR,
                    message=f"Failed to resolve Group context for DiagnosticReport: {exc}",
                )

        if report_texts.llm_config is None:
            group_id = report_texts.group_context.id if report_texts.group_context else None
            try:
                resolved_llm = self.llm_config_service.resolve_config_for_group_sync(
                    tag=tag,
                    group_id=group_id,
                    test_id=effective_test_id,
                )
            except Exception as exc:
                logger.error(
                    "diagnostic_report_llm_config_resolution_failed",
                    diagnostic_report_id=getattr(report_texts.report, "id", None),
                    group_id=group_id,
                    tag=tag,
                    error=str(exc),
                    exc_info=True,
                )
                return Result[ReportTexts](
                    status=STATUS_INTERNAL_ERROR,
                    message=f"Failed to resolve LLM_Config for tag '{tag}' and group '{group_id}': {exc}",
                )

            if resolved_llm is None:
                logger.error(
                    "diagnostic_report_llm_config_not_found",
                    diagnostic_report_id=getattr(report_texts.report, "id", None),
                    group_id=group_id,
                    tag=tag,
                )
                return Result[ReportTexts](
                    status=STATUS_BAD_DATA,
                    message=f"No active LLM_Config found for tag '{tag}' (group_id='{group_id}')",
                )

            report_texts.llm_config = resolved_llm

        return Result[ReportTexts](status=STATUS_OK, message="Configuration ensured", value=report_texts)

    def parse_diagnostic_report(
        self,
        diagnostic_report: DiagnosticReport,
        group_context: Optional[Group] = None,
        llm_config: Optional[LLM_Config] = None,
    ) -> ReportTexts:
        """
        Parses a DiagnosticReport object into a ReportTexts object.
        """
        result = ReportTexts(report=diagnostic_report, group_context=group_context, llm_config=llm_config)
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

    async def compute_embeddings(
        self,
        report_texts: ReportTexts,
        *,
        tag: str = DEFAULT_EMBEDDING_TAG,
        test_id: Optional[str] = None,
    ) -> Result[ReportTexts]:
        """Compute L2-normalized findings and conclusion embeddings for a ReportTexts object.

        Ensures ``group_context`` and ``llm_config`` are resolved first via
        :meth:`config_ensure`. Non-empty ``findings`` and ``conclusion`` strings
        are embedded concurrently with :meth:`EmbeddingService.embed_text_async`;
        empty or whitespace-only strings produce empty (0-element) vectors.
        """
        result = Result[ReportTexts](value=report_texts)
        cfg_result = self.config_ensure(report_texts, tag=tag, test_id=test_id)
        if cfg_result.status != STATUS_OK:
            result.status = cfg_result.status
            result.message = cfg_result.message
            return result

        findings_text = (report_texts.findings or "").strip()
        conclusion_text = (report_texts.conclusion or "").strip()

        if not findings_text and not conclusion_text:
            report_texts.embedding_findings = []
            report_texts.embedding_conclusion = []
            result.status = STATUS_OK
            result.message = "Vector embeddings Computed"
            return result

        group_id = report_texts.group_context.id if report_texts.group_context else None
        effective_test_id = (
            test_id
            or (report_texts.group_context.test_id if report_texts.group_context else None)
            or (report_texts.llm_config.test_id if report_texts.llm_config else None)
        )

        async def _embed(text: str) -> list[float]:
            return await self.embedding_service.embed_text_async(
                text,
                is_query=False,
                group_id=group_id,
                tag=tag,
                test_id=effective_test_id,
                title="none",
                llm_config=report_texts.llm_config,
            )

        try:
            if findings_text and conclusion_text:
                findings_vec, conclusion_vec = await asyncio.gather(
                    _embed(findings_text),
                    _embed(conclusion_text),
                )
            elif findings_text:
                findings_vec = await _embed(findings_text)
                conclusion_vec = []
            else:
                findings_vec = []
                conclusion_vec = await _embed(conclusion_text)
        except ValueError as exc:
            report_texts.embedding_findings = []
            report_texts.embedding_conclusion = []
            logger.error(
                "diagnostic_report_embedding_bad_data",
                diagnostic_report_id=getattr(report_texts.report, "id", None),
                group_id=group_id,
                tag=tag,
                error=str(exc),
            )
            result.status = STATUS_BAD_DATA
            result.message = f"Failed to compute vector embeddings: {exc}"
            return result
        except Exception as exc:
            report_texts.embedding_findings = []
            report_texts.embedding_conclusion = []
            logger.error(
                "diagnostic_report_embedding_failed",
                diagnostic_report_id=getattr(report_texts.report, "id", None),
                group_id=group_id,
                tag=tag,
                error=str(exc),
                exc_info=True,
            )
            result.status = STATUS_INTERNAL_ERROR
            result.message = f"Failed to compute vector embeddings: {exc}"
            return result

        report_texts.embedding_findings = findings_vec
        report_texts.embedding_conclusion = conclusion_vec
        result.status = STATUS_OK
        result.message = "Vector embeddings Computed"
        return result

    def graph_register(
        self,
        report_texts: ReportTexts,
        *,
        tag: str = DEFAULT_EMBEDDING_TAG,
        test_id: Optional[str] = None,
        acted_by: str = "diagnostic_report_service",
    ) -> Result[list[Embedding]]:
        """Idempotently register computed vector embeddings for a DiagnosticReport in Neo4j.

        Ensures ``group_context`` and ``llm_config`` via :meth:`config_ensure`,
        validates the target persisted ``:DiagnosticReport`` node exists and is active,
        and atomically upserts each non-empty ``:Embedding`` node along with its
        ``(:Embedding)-[:EMBEDS {aspect}]->(:DiagnosticReport)`` relationship.
        """
        result = Result[list[Embedding]]()
        cfg_result = self.config_ensure(report_texts, tag=tag, test_id=test_id)
        if cfg_result.status != STATUS_OK:
            result.status = cfg_result.status
            result.message = cfg_result.message
            return result

        raw_test_id = (
            test_id
            or (report_texts.group_context.test_id if report_texts.group_context else None)
            or (report_texts.llm_config.test_id if report_texts.llm_config else None)
        )
        effective_test_id = raw_test_id.strip() if isinstance(raw_test_id, str) and raw_test_id.strip() else None
        effective_acted_by = acted_by.strip() if isinstance(acted_by, str) and acted_by.strip() else "system"

        report_id = getattr(report_texts.report, "id", None)
        clean_report_id = report_id.strip() if isinstance(report_id, str) else ""
        if not clean_report_id:
            result.status = STATUS_BAD_DATA
            result.message = "DiagnosticReport.id is required to register vector embeddings in Neo4j"
            return result

        target_node_id = DiagnosticReportNode(
            resource_id=clean_report_id,
            resource="{}",
            test_id=effective_test_id,
        ).id

        model_tag = report_texts.llm_config.tag if report_texts.llm_config else tag

        section_vectors: list[tuple[DiagnosticReportFieldTag, list[float]]] = []
        if report_texts.embedding_findings:
            section_vectors.append((DiagnosticReportFieldTag.FINDINGS, report_texts.embedding_findings))
        if report_texts.embedding_conclusion:
            section_vectors.append((DiagnosticReportFieldTag.CONCLUSION, report_texts.embedding_conclusion))

        if not section_vectors:
            has_text = bool((report_texts.findings or "").strip() or (report_texts.conclusion or "").strip())
            if has_text:
                result.status = STATUS_BAD_DATA
                result.message = "No computed vector embeddings available to register; run compute_embeddings first"
                return result
            result.status = STATUS_OK
            result.message = "No vector embeddings to register"
            result.value = []
            return result

        embeddings: list[Embedding] = []
        tx_items: list[dict[str, Any]] = []
        try:
            for field_tag, vec in section_vectors:
                emb = Embedding(
                    target_node_id=target_node_id,
                    modelTag=model_tag,
                    size=len(vec),
                    vector=vec,
                    targetFieldTag=str(field_tag),
                    test_id=effective_test_id,
                    ActedBy=effective_acted_by,
                )
                if emb.size != DEFAULT_EMBEDDING_VECTOR_DIMENSIONS:
                    logger.warning(
                        "diagnostic_report_embedding_dimension_mismatch",
                        diagnostic_report_id=clean_report_id,
                        target_node_id=target_node_id,
                        field_tag=str(field_tag),
                        model_tag=model_tag,
                        vector_size=emb.size,
                        index_dimensions=DEFAULT_EMBEDDING_VECTOR_DIMENSIONS,
                    )
                rel = EmbedsRelation(
                    start_node_id=emb.id,
                    end_node_id=target_node_id,
                    aspect=str(field_tag),
                    test_id=effective_test_id,
                    ActedBy=effective_acted_by,
                )
                emb_props = emb.to_neo4j_properties()
                rel_props = rel.model_dump()
                if rel.ExpiresAt is not None:
                    rel_props["ExpiresAt"] = rel.ExpiresAt.isoformat()
                embeddings.append(emb)
                tx_items.append({"emb": emb_props, "rel": rel_props})
        except (ValidationError, ValueError) as exc:
            logger.error(
                "diagnostic_report_embedding_model_validation_failed",
                diagnostic_report_id=clean_report_id,
                target_node_id=target_node_id,
                model_tag=model_tag,
                error=str(exc),
            )
            result.status = STATUS_BAD_DATA
            result.message = f"Invalid embedding data for registration: {exc}"
            return result

        def _tx(tx: Any) -> list[dict[str, Any]]:
            res = tx.run(
                _UPSERT_REPORT_EMBEDDINGS_CYPHER,
                target_id=target_node_id,
                items=tx_items,
                acted_by=effective_acted_by,
            )
            return [record.data() for record in res]

        try:
            records = self.client.execute_write_tx_sync(_tx)
        except Exception as exc:
            logger.error(
                "diagnostic_report_embedding_register_failed",
                diagnostic_report_id=clean_report_id,
                target_node_id=target_node_id,
                model_tag=model_tag,
                error=str(exc),
                exc_info=True,
            )
            result.status = STATUS_INTERNAL_ERROR
            result.message = f"Failed to register vector embeddings in Neo4j: {exc}"
            return result

        if not records:
            logger.warning(
                "diagnostic_report_target_node_not_found",
                diagnostic_report_id=clean_report_id,
                target_node_id=target_node_id,
                test_id=effective_test_id,
            )
            result.status = STATUS_BAD_DATA
            result.message = (
                f"Active DiagnosticReport node not found in Neo4j for report_id '{clean_report_id}' "
                f"(node_id='{target_node_id}')"
            )
            return result

        logger.info(
            "diagnostic_report_embeddings_registered",
            diagnostic_report_id=clean_report_id,
            target_node_id=target_node_id,
            model_tag=model_tag,
            registered_count=len(records),
            embedding_ids=[rec.get("embedding_id") for rec in records],
        )
        result.status = STATUS_OK
        result.message = "Vector embeddings stored in the DB"
        result.value = embeddings
        return result
