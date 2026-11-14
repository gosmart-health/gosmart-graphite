"""FHIR integration service, batch fetcher, and Neo4j graph upsert for DICOM-FHIR graph server."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Union

import httpx
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.imagingstudy import ImagingStudy
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.procedure import Procedure
from fhir.resources.R4B.servicerequest import ServiceRequest
from pydantic import BaseModel, ConfigDict, Field

from core.config import get_settings
from core.logging import get_logger
from db.neo4j import Neo4jClient, neo4j_client
from model.fhir import (
    BasedOnRelation,
    BasedOnStudyRelation,
    ConditionNode,
    ContainsRelation,
    DiagnosticReportNode,
    EncounterNode,
    FHIRDomainResourceNode,
    FHIRPatientNode,
    FulfilledByRelation,
    HasEncounterRelation,
    ImagingStudyNode,
    ObservationNode,
    PerformedInRelation,
    ProcedureNode,
    ReferencesInstanceRelation,
    ReferencesSeriesRelation,
    ReferencesStudyRelation,
    SameAsRelation,
    ServiceRequestNode,
    SubjectRelation,
    SupportsReportRelation,
    flatten_codings,
    flatten_identifiers,
    format_coding_macro,
    format_reference_macro,
)
from model.graph import AuditableRelation
from service.graph_service import GraphService, graph_service

logger = get_logger("gosmart_gs.fhir_service")

_AUDIT_INTERNAL_FIELDS = frozenset({"id", "Action", "ActedAt", "ActedBy", "CreatedAt", "UpdatedAt", "ExpiresAt"})


class EncounterContext(BaseModel):
    """Raw JSON-compatible dictionary context of an encounter and all related FHIR resources."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    encounter: Union[dict[str, Any], Encounter]
    patient: Optional[dict[str, Any]] = None
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    diagnostic_reports: list[dict[str, Any]] = Field(default_factory=list)
    imaging_studies: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    procedures: list[dict[str, Any]] = Field(default_factory=list)
    serviceRequests: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EncRelevantResources(BaseModel):
    """Validated strongly-typed FHIR domain resources related to an Encounter."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    encounter: Optional[Encounter] = None
    patient: Optional[Patient] = None
    conditions: list[Condition] = Field(default_factory=list)
    diagnostic_reports: list[DiagnosticReport] = Field(default_factory=list)
    imaging_studies: list[ImagingStudy] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    serviceRequests: list[ServiceRequest] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# =============================================================================
# FHIR R4B Resource -> FHIRDomainResourceNode Conversion Helpers
# =============================================================================


def _extract_reference(ref_obj: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Convert a raw FHIR Reference dict into a Reference Macro ('reference|display' or 'reference')."""
    if not isinstance(ref_obj, Mapping):
        return None
    macro = format_reference_macro(ref_obj.get("reference"), ref_obj.get("display"))
    return macro if macro else None


def _extract_references(ref_list: Optional[Sequence[Mapping[str, Any]]]) -> Optional[list[str]]:
    """Convert a sequence of raw FHIR Reference dicts into a list of Reference Macros."""
    if not ref_list:
        return None
    macros = [m for item in ref_list if (m := _extract_reference(item))]
    return macros if macros else None


def _extract_identifier_types(identifiers: Optional[Sequence[Mapping[str, Any]]]) -> Optional[list[str]]:
    """Extract and flatten Identifier.type CodeableConcepts from a list of FHIR Identifiers."""
    if not identifiers:
        return None
    types = [
        ident["type"] for ident in identifiers if isinstance(ident, Mapping) and isinstance(ident.get("type"), Mapping)
    ]
    codings = flatten_codings(types)
    return codings if codings else None


def _normalize_reference_target(ref_macro: Optional[str], default_type: Optional[str] = None) -> Optional[str]:
    """Normalize a Reference Macro (e.g. 'Patient/3426|Jane Doe') into 'ResourceType/id'."""
    if not ref_macro:
        return None
    ref_part = ref_macro.split("|", 1)[0].strip().rstrip("/")
    if not ref_part:
        return None
    if "/" in ref_part:
        parts = [p for p in ref_part.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
    if default_type:
        return f"{default_type}/{ref_part}"
    return ref_part


def _format_human_names(names: Optional[Sequence[Mapping[str, Any]]]) -> Optional[list[str]]:
    """Format a list of FHIR HumanName dicts into human-readable strings."""
    if not names:
        return None
    formatted: list[str] = []
    for n in names:
        if not isinstance(n, Mapping):
            continue
        text = (n.get("text") or "").strip()
        if text:
            formatted.append(text)
            continue
        parts: list[str] = []
        for prefix in n.get("prefix") or []:
            if prefix:
                parts.append(str(prefix).strip())
        for given in n.get("given") or []:
            if given:
                parts.append(str(given).strip())
        family = (n.get("family") or "").strip()
        if family:
            parts.append(family)
        for suffix in n.get("suffix") or []:
            if suffix:
                parts.append(str(suffix).strip())
        joined = " ".join(p for p in parts if p)
        if joined:
            formatted.append(joined)
    return formatted if formatted else None


def patient_to_node(
    patient: Patient,
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> FHIRPatientNode:
    """Convert a FHIR R4B Patient resource into a FHIRPatientNode."""
    raw = patient.model_dump(mode="json", exclude_none=True)
    raw_json = patient.model_dump_json(exclude_none=True)
    res_id = str(raw.get("id") or "")
    active = raw.get("active")
    status = "active" if active is True else ("inactive" if active is False else raw.get("status"))
    return FHIRPatientNode(
        resource_id=res_id,
        status=status,
        resource=raw_json,
        identifiers=flatten_identifiers(raw.get("identifier")),
        identifierType=_extract_identifier_types(raw.get("identifier")),
        active=active,
        name=_format_human_names(raw.get("name")),
        gender=raw.get("gender"),
        birthDate=str(raw["birthDate"]) if raw.get("birthDate") is not None else None,
        managingOrganization=_extract_reference(raw.get("managingOrganization")),
        test_id=test_id,
        ActedBy=acted_by,
    )


def encounter_to_node(
    encounter: Encounter,
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> EncounterNode:
    """Convert a FHIR R4B Encounter resource into an EncounterNode."""
    raw = encounter.model_dump(mode="json", exclude_none=True)
    raw_json = encounter.model_dump_json(exclude_none=True)
    res_id = str(raw.get("id") or "")

    cls_dict = raw.get("class")
    class_macro: Optional[str] = None
    if isinstance(cls_dict, Mapping):
        class_macro = format_coding_macro(
            cls_dict.get("system"),
            cls_dict.get("code"),
            cls_dict.get("display"),
        )

    participants = raw.get("participant") or []
    part_types = flatten_codings([t for p in participants if isinstance(p, Mapping) for t in (p.get("type") or [])])
    part_starts = [
        p["period"]["start"]
        for p in participants
        if isinstance(p, Mapping) and isinstance(p.get("period"), Mapping) and p["period"].get("start") is not None
    ]
    part_ends = [
        p["period"]["end"]
        for p in participants
        if isinstance(p, Mapping) and isinstance(p.get("period"), Mapping) and p["period"].get("end") is not None
    ]
    part_individuals = [
        ref for p in participants if isinstance(p, Mapping) and (ref := _extract_reference(p.get("individual")))
    ]

    locations = [
        ref
        for loc in (raw.get("location") or [])
        if isinstance(loc, Mapping) and (ref := _extract_reference(loc.get("location")))
    ]

    period = raw.get("period") if isinstance(raw.get("period"), Mapping) else {}

    return EncounterNode(
        resource_id=res_id,
        status=raw.get("status"),
        resource=raw_json,
        identifiers=flatten_identifiers(raw.get("identifier")),
        **{"class": class_macro},
        type=flatten_codings(raw.get("type")),
        subject=_extract_reference(raw.get("subject")),
        participantType=part_types if part_types else None,
        participantPeriodStart=part_starts if part_starts else None,
        participantPeriodEnd=part_ends if part_ends else None,
        participantIndividual=part_individuals if part_individuals else None,
        periodStart=period.get("start"),
        periodEnd=period.get("end"),
        location=locations if locations else None,
        serviceProvider=_extract_reference(raw.get("serviceProvider")),
        test_id=test_id,
        ActedBy=acted_by,
    )


def condition_to_node(
    condition: Condition,
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> ConditionNode:
    """Convert a FHIR R4B Condition resource into a ConditionNode."""
    raw = condition.model_dump(mode="json", exclude_none=True)
    raw_json = condition.model_dump_json(exclude_none=True)
    res_id = str(raw.get("id") or "")

    clin_status = flatten_codings(raw.get("clinicalStatus"))
    status_val: Optional[str] = raw.get("status")
    if not status_val and isinstance(raw.get("clinicalStatus"), Mapping):
        codings = raw["clinicalStatus"].get("coding") or []
        if codings and isinstance(codings[0], Mapping):
            status_val = codings[0].get("code")

    idents = flatten_identifiers(raw.get("identifier"))

    return ConditionNode(
        resource_id=res_id,
        status=status_val,
        resource=raw_json,
        identifiers=idents if idents else None,
        clinicalStatus=clin_status,
        verificationStatus=flatten_codings(raw.get("verificationStatus")),
        category=flatten_codings(raw.get("category")),
        code=flatten_codings(raw.get("code")),
        subject=_extract_reference(raw.get("subject")),
        encounter=_extract_reference(raw.get("encounter")),
        onsetDateTime=raw.get("onsetDateTime"),
        abatementDateTime=raw.get("abatementDateTime"),
        recordedDate=raw.get("recordedDate"),
        test_id=test_id,
        ActedBy=acted_by,
    )


def imaging_study_to_node(
    imaging_study: ImagingStudy,
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> ImagingStudyNode:
    """Convert a FHIR R4B ImagingStudy resource into an ImagingStudyNode."""
    raw = imaging_study.model_dump(mode="json", exclude_none=True)
    raw_json = imaging_study.model_dump_json(exclude_none=True)
    res_id = str(raw.get("id") or "")

    raw_identifiers = raw.get("identifier") or []
    ident_uses = [str(i["use"]) for i in raw_identifiers if isinstance(i, Mapping) and i.get("use")]

    study_uid: Optional[str] = None
    for ident in raw_identifiers:
        if not isinstance(ident, Mapping):
            continue
        val = str(ident.get("value") or "").strip()
        sys_val = str(ident.get("system") or "").strip()
        if val.startswith("urn:oid:"):
            study_uid = val.replace("urn:oid:", "").strip()
            break
        if sys_val in ("urn:dicom:uid", "urn:ietf:rfc:3986") and val:
            study_uid = val.replace("urn:oid:", "").strip()
            break

    proc_codes_raw = raw.get("procedureCode") or []
    proc_codings = flatten_codings(proc_codes_raw)
    proc_text: Optional[str] = None
    for pc in proc_codes_raw:
        if isinstance(pc, Mapping) and pc.get("text"):
            proc_text = str(pc["text"])
            break

    series_list = raw.get("series") or []
    series_uids: list[str] = []
    instance_uids: list[str] = []
    modalities_raw: list[Mapping[str, Any]] = []
    body_sites_raw: list[Mapping[str, Any]] = []
    series_started: list[Any] = []

    for s in series_list:
        if not isinstance(s, Mapping):
            continue
        s_uid = str(s.get("uid") or "").replace("urn:oid:", "").strip()
        if s_uid:
            series_uids.append(s_uid)
        if isinstance(s.get("modality"), Mapping):
            modalities_raw.append(s["modality"])
        if isinstance(s.get("bodySite"), Mapping):
            body_sites_raw.append(s["bodySite"])
        if s.get("started") is not None:
            series_started.append(s["started"])
        for inst in s.get("instance") or []:
            if isinstance(inst, Mapping):
                i_uid = str(inst.get("uid") or "").replace("urn:oid:", "").strip()
                if i_uid:
                    instance_uids.append(i_uid)

    body_sites = flatten_codings(body_sites_raw)

    return ImagingStudyNode(
        resource_id=res_id,
        status=raw.get("status"),
        resource=raw_json,
        identifiers=flatten_identifiers(raw_identifiers),
        identifierType=_extract_identifier_types(raw_identifiers),
        identifierUse=ident_uses if ident_uses else None,
        subject=_extract_reference(raw.get("subject")),
        encounter=_extract_reference(raw.get("encounter")),
        basedOn=_extract_references(raw.get("basedOn")),
        started=raw.get("started"),
        procedureCode=proc_codings if proc_codings else None,
        procedureCodeText=proc_text,
        location=_extract_reference(raw.get("location")),
        studyUid=study_uid,
        seriesUid=series_uids if series_uids else None,
        instanceUid=instance_uids if instance_uids else None,
        seriesModality=flatten_codings(modalities_raw),
        bodySite=body_sites if body_sites else None,
        seriesStarted=series_started if series_started else None,
        test_id=test_id,
        ActedBy=acted_by,
    )


def observation_to_node(
    observation: Observation,
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> ObservationNode:
    """Convert a FHIR R4B Observation resource into an ObservationNode."""
    raw = observation.model_dump(mode="json", exclude_none=True)
    raw_json = observation.model_dump_json(exclude_none=True)
    res_id = str(raw.get("id") or "")

    obs_codings = flatten_codings(raw.get("code"))
    codes: list[str] = []
    numeric_values: list[float] = []
    units: list[str] = []

    vq = raw.get("valueQuantity")
    if isinstance(vq, Mapping) and isinstance(vq.get("value"), (int, float)):
        primary_code = obs_codings[0] if obs_codings else ""
        unit_macro = format_coding_macro(
            vq.get("system"),
            vq.get("code") or vq.get("unit"),
            vq.get("unit"),
        )
        codes.append(primary_code)
        numeric_values.append(float(vq["value"]))
        units.append(unit_macro)

    for comp in raw.get("component") or []:
        if not isinstance(comp, Mapping):
            continue
        cvq = comp.get("valueQuantity")
        if isinstance(cvq, Mapping) and isinstance(cvq.get("value"), (int, float)):
            comp_codings = flatten_codings(comp.get("code"))
            comp_code = comp_codings[0] if comp_codings else (obs_codings[0] if obs_codings else "")
            comp_unit = format_coding_macro(
                cvq.get("system"),
                cvq.get("code") or cvq.get("unit"),
                cvq.get("unit"),
            )
            codes.append(comp_code)
            numeric_values.append(float(cvq["value"]))
            units.append(comp_unit)

    idents = flatten_identifiers(raw.get("identifier"))
    body_sites = flatten_codings(raw.get("bodySite"))
    text_val = raw.get("valueString") or (raw.get("code", {}) if isinstance(raw.get("code"), Mapping) else {}).get(
        "text"
    )

    return ObservationNode(
        resource_id=res_id,
        status=raw.get("status"),
        resource=raw_json,
        identifiers=idents if idents else None,
        basedOn=_extract_references(raw.get("basedOn")),
        encounter=_extract_reference(raw.get("encounter")),
        code=obs_codings,
        subject=_extract_reference(raw.get("subject")),
        performer=_extract_references(raw.get("performer")),
        codes=codes if codes else None,
        numericValues=numeric_values if numeric_values else None,
        units=units if units else None,
        bodySite=body_sites if body_sites else None,
        text=str(text_val) if text_val else None,
        test_id=test_id,
        ActedBy=acted_by,
    )


def diagnostic_report_to_node(
    report: DiagnosticReport,
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> DiagnosticReportNode:
    """Convert a FHIR R4B DiagnosticReport resource into a DiagnosticReportNode."""
    raw = report.model_dump(mode="json", exclude_none=True)
    raw_json = report.model_dump_json(exclude_none=True)
    res_id = str(raw.get("id") or "")

    raw_identifiers = raw.get("identifier") or []
    idents = flatten_identifiers(raw_identifiers)
    categories = flatten_codings(raw.get("category"))
    text_obj = raw.get("text") if isinstance(raw.get("text"), Mapping) else {}
    presented_data = [
        str(pf["data"]) for pf in (raw.get("presentedForm") or []) if isinstance(pf, Mapping) and pf.get("data")
    ]

    return DiagnosticReportNode(
        resource_id=res_id,
        status=raw.get("status"),
        resource=raw_json,
        identifiers=idents if idents else None,
        identifierType=_extract_identifier_types(raw_identifiers),
        basedOn=_extract_references(raw.get("basedOn")),
        imagingStudy=_extract_references(raw.get("imagingStudy")),
        category=categories if categories else None,
        code=flatten_codings(raw.get("code")),
        subject=_extract_reference(raw.get("subject")),
        encounter=_extract_reference(raw.get("encounter")),
        effectiveDateTime=raw.get("effectiveDateTime"),
        issued=raw.get("issued"),
        performer=_extract_references(raw.get("performer")),
        result=_extract_references(raw.get("result")),
        conclusion=raw.get("conclusion"),
        textStatus=text_obj.get("status"),
        textDiv=text_obj.get("div"),
        presentedFormData=presented_data if presented_data else None,
        test_id=test_id,
        ActedBy=acted_by,
    )


def procedure_to_node(
    procedure: Procedure,
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> ProcedureNode:
    """Convert a FHIR R4B Procedure resource into a ProcedureNode."""
    raw = procedure.model_dump(mode="json", exclude_none=True)
    raw_json = procedure.model_dump_json(exclude_none=True)
    res_id = str(raw.get("id") or "")

    raw_identifiers = raw.get("identifier") or []
    idents = flatten_identifiers(raw_identifiers)
    categories = flatten_codings(raw.get("category"))
    reason_codes = flatten_codings(raw.get("reasonCode"))
    body_sites = flatten_codings(raw.get("bodySite"))

    performed_dt = raw.get("performedDateTime")
    if performed_dt is None and isinstance(raw.get("performedPeriod"), Mapping):
        performed_dt = raw["performedPeriod"].get("start")

    performers = [
        ref
        for p in (raw.get("performer") or [])
        if isinstance(p, Mapping) and (ref := _extract_reference(p.get("actor")))
    ]

    return ProcedureNode(
        resource_id=res_id,
        status=raw.get("status"),
        resource=raw_json,
        identifiers=idents if idents else None,
        identifierType=_extract_identifier_types(raw_identifiers),
        basedOn=_extract_references(raw.get("basedOn")),
        partOf=_extract_references(raw.get("partOf")),
        category=categories if categories else None,
        code=flatten_codings(raw.get("code")),
        subject=_extract_reference(raw.get("subject")),
        encounter=_extract_reference(raw.get("encounter")),
        performedDateTime=performed_dt,
        recorder=_extract_reference(raw.get("recorder")),
        asserter=_extract_reference(raw.get("asserter")),
        performer=performers if performers else None,
        location=_extract_reference(raw.get("location")),
        reasonCode=reason_codes if reason_codes else None,
        bodySite=body_sites if body_sites else None,
        report=_extract_references(raw.get("report")),
        test_id=test_id,
        ActedBy=acted_by,
    )


def service_request_to_node(
    service_request: ServiceRequest,
    test_id: Optional[str] = None,
    acted_by: str = "system",
) -> ServiceRequestNode:
    """Convert a FHIR R4B ServiceRequest resource into a ServiceRequestNode."""
    raw = service_request.model_dump(mode="json", exclude_none=True)
    raw_json = service_request.model_dump_json(exclude_none=True)
    res_id = str(raw.get("id") or "")

    raw_identifiers = raw.get("identifier") or []
    categories = flatten_codings(raw.get("category"))
    reason_codes = flatten_codings(raw.get("reasonCode"))
    body_sites_raw = raw.get("bodySite") or []
    body_sites = flatten_codings(body_sites_raw)
    body_site_text: Optional[str] = None
    for bs in body_sites_raw:
        if isinstance(bs, Mapping) and bs.get("text"):
            body_site_text = str(bs["text"])
            break

    code_obj = raw.get("code") if isinstance(raw.get("code"), Mapping) else {}
    occ_dt = raw.get("occurrenceDateTime")
    if occ_dt is None and isinstance(raw.get("occurrencePeriod"), Mapping):
        occ_dt = raw["occurrencePeriod"].get("start")

    return ServiceRequestNode(
        resource_id=res_id,
        status=raw.get("status"),
        resource=raw_json,
        identifiers=flatten_identifiers(raw_identifiers),
        identifierType=_extract_identifier_types(raw_identifiers),
        intent=raw.get("intent") or "order",
        category=categories if categories else None,
        priority=raw.get("priority"),
        code=flatten_codings(code_obj),
        codeText=code_obj.get("text"),
        subject=_extract_reference(raw.get("subject")),
        encounter=_extract_reference(raw.get("encounter")),
        occurrenceDateTime=occ_dt,
        authoredOn=raw.get("authoredOn"),
        requester=_extract_reference(raw.get("requester")),
        reasonCode=reason_codes if reason_codes else None,
        bodySite=body_sites if body_sites else None,
        bodySiteText=body_site_text,
        patientInstruction=raw.get("patientInstruction"),
        supportingInfo=_extract_references(raw.get("supportingInfo")),
        test_id=test_id,
        ActedBy=acted_by,
    )


class FHIRBatchFetcher:
    """Handles low-level async HTTP requests and concurrent batch queries against FHIR R4 server."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        timeout: float = 30.0,
    ) -> None:
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        else:
            settings = get_settings()
            self.base_url = (getattr(settings, "FHIR_BASE_URL", "") or "").rstrip("/")
        self._custom_client: Optional[httpx.AsyncClient] = client
        self._client: Optional[httpx.AsyncClient] = client
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._timeout: float = timeout

    @property
    def client(self) -> httpx.AsyncClient:
        if self._custom_client is not None:
            return self._custom_client

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._client is None or self._client.is_closed or self._loop != current_loop:
            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._loop = current_loop

        return self._client

    @client.setter
    def client(self, client: Optional[httpx.AsyncClient]) -> None:
        self._custom_client = client
        self._client = client

    async def close(self) -> None:
        """Close the underlying HTTP client if opened."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            self._loop = None

    async def __aenter__(self) -> FHIRBatchFetcher:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def fetch_resource_bundle(self, path: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Fetch a FHIR searchset bundle and return the list of resource dicts."""
        if not self.base_url:
            logger.warning("FHIR base URL is not configured")
            return []

        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = await self.client.get(url, params=params)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        bundle = resp.json()
        if not isinstance(bundle, dict):
            return []
        return [
            entry["resource"]
            for entry in bundle.get("entry", [])
            if isinstance(entry, dict) and "resource" in entry and isinstance(entry["resource"], dict)
        ]

    async def fetch_by_reference(self, reference: str) -> Optional[dict[str, Any]]:
        """Fetch a single resource by its reference string or full URL."""
        if not reference or not self.base_url:
            return None

        ref = reference.strip()
        if ref.startswith("http://") or ref.startswith("https://"):
            url = ref
        else:
            url = f"{self.base_url}/{ref.lstrip('/')}"

        resp = await self.client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None

    async def fetch_resource(self, resource_type: str, resource_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single resource by its resource type and ID."""
        clean_id = resource_id.strip("/").split("/")[-1]
        clean_type = resource_type.strip("/")
        return await self.fetch_by_reference(f"{clean_type}/{clean_id}")

    async def fetch_encounter_graph(self, encounter: Union[Encounter, dict[str, Any]]) -> EncounterContext:
        """Concurrently fetch all resources related to an Encounter."""
        if isinstance(encounter, Encounter):
            enc_id = getattr(encounter, "id", None)
            patient_ref = encounter.subject.reference if getattr(encounter, "subject", None) else None
            enc_dict = encounter.model_dump(mode="json", exclude_none=True)
        elif isinstance(encounter, dict):
            enc_id = encounter.get("id")
            subject = encounter.get("subject")
            patient_ref = subject.get("reference") if isinstance(subject, dict) else None
            enc_dict = encounter
        else:
            raise ValueError(f"Unsupported encounter type: {type(encounter)}")

        if not enc_id:
            raise ValueError("Encounter must have a valid 'id' to fetch related graph")

        tasks: dict[str, Any] = {
            "conditions": self.fetch_resource_bundle("Condition", {"encounter": enc_id}),
            "diagnostic_reports": self.fetch_resource_bundle("DiagnosticReport", {"encounter": enc_id}),
            "imaging_studies": self.fetch_resource_bundle("ImagingStudy", {"encounter": enc_id}),
            "observations": self.fetch_resource_bundle("Observation", {"encounter": enc_id}),
            "procedures": self.fetch_resource_bundle("Procedure", {"encounter": enc_id}),
            "serviceRequests": self.fetch_resource_bundle("ServiceRequest", {"encounter": enc_id}),
        }

        if patient_ref:
            tasks["patient"] = self.fetch_by_reference(patient_ref)

        task_keys = list(tasks.keys())
        task_coroutines = list(tasks.values())

        results = await asyncio.gather(*task_coroutines, return_exceptions=True)

        context_data: dict[str, Any] = {
            "encounter": enc_dict,
            "patient": None,
            "conditions": [],
            "diagnostic_reports": [],
            "imaging_studies": [],
            "observations": [],
            "procedures": [],
            "serviceRequests": [],
        }
        errors: list[str] = []

        for key, res in zip(task_keys, results):
            if isinstance(res, Exception):
                errors.append(f"Failed to fetch {key}: {res}")
            else:
                context_data[key] = res

        return EncounterContext(**context_data, errors=errors)


class FhirService:
    """High-level FHIR domain service for DICOM and clinical encounter workflows."""

    def __init__(
        self,
        fetcher: Optional[FHIRBatchFetcher] = None,
        client: Optional[Neo4jClient] = None,
        graph_svc: Optional[GraphService] = None,
    ) -> None:
        self.fetcher: FHIRBatchFetcher = fetcher if fetcher is not None else FHIRBatchFetcher()
        self.client: Neo4jClient = client if client is not None else neo4j_client
        self.graph_service: GraphService = graph_svc if graph_svc is not None else graph_service

    async def fetch_imaging_studies(self, study_uid: str) -> list[ImagingStudy]:
        """Fetch all ImagingStudies matching a DICOM Study Instance UID or FHIR ID."""
        if not study_uid:
            return []

        bare_uid = study_uid.replace("urn:oid:", "").strip()
        search_val = f"{bare_uid},urn:oid:{bare_uid}"

        bundle = await self.fetcher.fetch_resource_bundle("ImagingStudy", {"identifier": search_val})
        if not bundle:
            # Fallback to direct ID search if identifier query returned nothing
            bundle = await self.fetcher.fetch_resource_bundle("ImagingStudy", {"_id": bare_uid})

        return [ImagingStudy.model_validate(r) for r in bundle]

    async def fetch_imaging_study(self, study_uid: str) -> Optional[ImagingStudy]:
        """Fetch an ImagingStudy by DICOM Study Instance UID or FHIR ID."""
        studies = await self.fetch_imaging_studies(study_uid)
        return studies[0] if studies else None

    async def fetch_encounter(self, encounter_id: str) -> Optional[Encounter]:
        """Fetch an Encounter resource by ID."""
        if not encounter_id:
            return None

        clean_id = encounter_id.strip("/").split("/")[-1]
        bundle = await self.fetcher.fetch_resource_bundle("Encounter", {"_id": clean_id})
        if not bundle:
            return None

        return Encounter.model_validate(bundle[0])

    async def fetch_patient(self, patient_id_or_ref: str) -> Optional[Patient]:
        """Fetch a Patient resource by ID or reference string."""
        if not patient_id_or_ref:
            return None

        data = await self.fetcher.fetch_by_reference(patient_id_or_ref)
        if not data:
            return None

        return Patient.model_validate(data)

    async def fetch_enc_diagnostic_reports(self, encounter_id: str) -> list[DiagnosticReport]:
        """Fetch all DiagnosticReports associated with an encounter."""
        if not encounter_id:
            return []

        clean_id = encounter_id.strip("/").split("/")[-1]
        bundle = await self.fetcher.fetch_resource_bundle("DiagnosticReport", {"encounter": clean_id})
        return [DiagnosticReport.model_validate(r) for r in bundle]

    async def fetch_enc_diagnostic_report(self, encounter_id: str) -> Optional[DiagnosticReport]:
        """Fetch the primary DiagnosticReport associated with an encounter."""
        reports = await self.fetch_enc_diagnostic_reports(encounter_id)
        return reports[0] if reports else None

    async def fetch_enc_relevant_resources(self, enc: Union[Encounter, str]) -> EncRelevantResources:
        """Fetch all relevant resources for an encounter graph."""
        if isinstance(enc, str):
            encounter_obj = await self.fetch_encounter(enc)
            if encounter_obj is None:
                raise ValueError(f"Encounter not found for ID: {enc}")
            enc = encounter_obj

        resources = await self.fetcher.fetch_encounter_graph(encounter=enc)

        patient_obj: Optional[Patient] = None
        if resources.patient:
            try:
                patient_obj = Patient.model_validate(resources.patient)
            except Exception as e:
                logger.warning("failed_to_validate_patient", error=str(e))

        conditions = [Condition.model_validate(z) for z in resources.conditions]
        diagnostic_reports = [DiagnosticReport.model_validate(z) for z in resources.diagnostic_reports]
        imaging_studies = [ImagingStudy.model_validate(z) for z in resources.imaging_studies]
        observations = [Observation.model_validate(z) for z in resources.observations]
        procedures = [Procedure.model_validate(z) for z in resources.procedures]
        service_requests = [ServiceRequest.model_validate(z) for z in resources.serviceRequests]

        return EncRelevantResources(
            encounter=enc,
            patient=patient_obj,
            conditions=conditions,
            diagnostic_reports=diagnostic_reports,
            imaging_studies=imaging_studies,
            observations=observations,
            procedures=procedures,
            serviceRequests=service_requests,
            errors=resources.errors,
        )

    async def fetch_relevant_resources_of_study(self, study_uid: str) -> EncRelevantResources:
        """Fetch all relevant clinical resources for an Encounter associated with a DICOM study UID."""
        if not study_uid:
            return EncRelevantResources(errors=["failed_to_fetch_imaging_study: empty study_uid"])

        imaging_studies = await self.fetch_imaging_studies(study_uid=study_uid)
        if not imaging_studies:
            return EncRelevantResources(errors=[f"failed_to_fetch_imaging_study: {study_uid}"])

        errors: list[str] = []
        count = len(imaging_studies)
        if count != 1:
            errors.append(f"{count} imaging studies, expected 1: {study_uid}")

        imaging: ImagingStudy = imaging_studies[0]
        enc_id: Optional[str] = None
        if getattr(imaging, "encounter", None):
            if getattr(imaging.encounter, "reference", None):
                enc_id = imaging.encounter.reference.strip("/").split("/")[-1]
            elif getattr(imaging.encounter, "id", None):
                enc_id = imaging.encounter.id

        if not enc_id:
            errors.append(f"failed_to_fetch_encounter ref: {study_uid}")
            return EncRelevantResources(imaging_studies=imaging_studies, errors=errors)

        encounter = await self.fetch_encounter(encounter_id=enc_id)
        if not encounter:
            errors.append(f"failed_to_fetch_encounter: {enc_id}")
            return EncRelevantResources(imaging_studies=imaging_studies, errors=errors)

        result = await self.fetch_enc_relevant_resources(enc=encounter)
        if errors:
            result.errors = errors + result.errors

        # Ensure the fetched imaging studies are present in result.imaging_studies
        existing_ids = {getattr(s, "id", None) for s in result.imaging_studies}
        for st in imaging_studies:
            if getattr(st, "id", None) not in existing_ids:
                result.imaging_studies.append(st)

        return result

    # -------------------------------------------------------------------------
    # Graph Conversion, Safe Upsert & Cleanup
    # -------------------------------------------------------------------------

    def convert_relevant_resources(
        self,
        resources: EncRelevantResources,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> tuple[list[FHIRDomainResourceNode], list[AuditableRelation]]:
        """Convert EncRelevantResources into FHIRDomainResourceNode and Intra-FHIR AuditableRelation instances."""
        nodes: list[FHIRDomainResourceNode] = []
        patient_map: dict[str, FHIRPatientNode] = {}
        encounter_map: dict[str, EncounterNode] = {}
        condition_map: dict[str, ConditionNode] = {}
        imaging_study_map: dict[str, ImagingStudyNode] = {}
        observation_map: dict[str, ObservationNode] = {}
        diagnostic_report_map: dict[str, DiagnosticReportNode] = {}
        procedure_map: dict[str, ProcedureNode] = {}
        service_request_map: dict[str, ServiceRequestNode] = {}

        if resources.patient is not None and getattr(resources.patient, "id", None):
            p_node = patient_to_node(resources.patient, test_id=test_id, acted_by=acted_by)
            patient_map[f"Patient/{p_node.resource_id}"] = p_node
            nodes.append(p_node)

        if resources.encounter is not None and getattr(resources.encounter, "id", None):
            e_node = encounter_to_node(resources.encounter, test_id=test_id, acted_by=acted_by)
            encounter_map[f"Encounter/{e_node.resource_id}"] = e_node
            nodes.append(e_node)

        for cond in resources.conditions:
            if not getattr(cond, "id", None):
                continue
            c_node = condition_to_node(cond, test_id=test_id, acted_by=acted_by)
            key = f"Condition/{c_node.resource_id}"
            if key not in condition_map:
                condition_map[key] = c_node
                nodes.append(c_node)

        for sr in resources.serviceRequests:
            if not getattr(sr, "id", None):
                continue
            sr_node = service_request_to_node(sr, test_id=test_id, acted_by=acted_by)
            key = f"ServiceRequest/{sr_node.resource_id}"
            if key not in service_request_map:
                service_request_map[key] = sr_node
                nodes.append(sr_node)

        for img in resources.imaging_studies:
            if not getattr(img, "id", None):
                continue
            is_node = imaging_study_to_node(img, test_id=test_id, acted_by=acted_by)
            key = f"ImagingStudy/{is_node.resource_id}"
            if key not in imaging_study_map:
                imaging_study_map[key] = is_node
                nodes.append(is_node)

        for obs in resources.observations:
            if not getattr(obs, "id", None):
                continue
            obs_node = observation_to_node(obs, test_id=test_id, acted_by=acted_by)
            key = f"Observation/{obs_node.resource_id}"
            if key not in observation_map:
                observation_map[key] = obs_node
                nodes.append(obs_node)

        for dr in resources.diagnostic_reports:
            if not getattr(dr, "id", None):
                continue
            dr_node = diagnostic_report_to_node(dr, test_id=test_id, acted_by=acted_by)
            key = f"DiagnosticReport/{dr_node.resource_id}"
            if key not in diagnostic_report_map:
                diagnostic_report_map[key] = dr_node
                nodes.append(dr_node)

        for proc in resources.procedures:
            if not getattr(proc, "id", None):
                continue
            proc_node = procedure_to_node(proc, test_id=test_id, acted_by=acted_by)
            key = f"Procedure/{proc_node.resource_id}"
            if key not in procedure_map:
                procedure_map[key] = proc_node
                nodes.append(proc_node)

        relations: list[AuditableRelation] = []
        seen_rel_ids: set[str] = set()

        def _add_rel(rel: AuditableRelation) -> None:
            if rel.id not in seen_rel_ids:
                seen_rel_ids.add(rel.id)
                relations.append(rel)

        # 1. (:SUBJECT) -> (:FHIR_Patient)
        subject_sources: list[FHIRDomainResourceNode] = [
            *encounter_map.values(),
            *service_request_map.values(),
            *diagnostic_report_map.values(),
            *imaging_study_map.values(),
            *condition_map.values(),
            *observation_map.values(),
            *procedure_map.values(),
        ]
        for src in subject_sources:
            subj_ref = _normalize_reference_target(getattr(src, "subject", None), "Patient")
            if subj_ref and subj_ref in patient_map:
                _add_rel(
                    SubjectRelation(
                        start_node_id=src.id,
                        end_node_id=patient_map[subj_ref].id,
                        test_id=test_id,
                        ActedBy=acted_by,
                    )
                )

        # 2. (:PERFORMED_IN) -> (:Encounter)
        performed_in_sources: list[FHIRDomainResourceNode] = [
            *imaging_study_map.values(),
            *procedure_map.values(),
            *observation_map.values(),
            *diagnostic_report_map.values(),
            *service_request_map.values(),
        ]
        for src in performed_in_sources:
            enc_ref = _normalize_reference_target(getattr(src, "encounter", None), "Encounter")
            if enc_ref and enc_ref in encounter_map:
                _add_rel(
                    PerformedInRelation(
                        start_node_id=src.id,
                        end_node_id=encounter_map[enc_ref].id,
                        test_id=test_id,
                        ActedBy=acted_by,
                    )
                )

        # 3. (:Condition)-[:HAS_ENCOUNTER]->(:Encounter)
        for c_node in condition_map.values():
            enc_ref = _normalize_reference_target(c_node.encounter, "Encounter")
            if enc_ref and enc_ref in encounter_map:
                _add_rel(
                    HasEncounterRelation(
                        start_node_id=c_node.id,
                        end_node_id=encounter_map[enc_ref].id,
                        test_id=test_id,
                        ActedBy=acted_by,
                    )
                )

        # 4. (:BASED_ON) -> (:ServiceRequest)
        based_on_sources: list[FHIRDomainResourceNode] = [
            *imaging_study_map.values(),
            *diagnostic_report_map.values(),
            *procedure_map.values(),
            *observation_map.values(),
        ]
        for src in based_on_sources:
            for ref in getattr(src, "basedOn", None) or []:
                sr_ref = _normalize_reference_target(ref, "ServiceRequest")
                if sr_ref and sr_ref in service_request_map:
                    _add_rel(
                        BasedOnRelation(
                            start_node_id=src.id,
                            end_node_id=service_request_map[sr_ref].id,
                            test_id=test_id,
                            ActedBy=acted_by,
                        )
                    )

        # 5. (:DiagnosticReport)-[:BASED_ON_STUDY]->(:ImagingStudy)
        for dr_node in diagnostic_report_map.values():
            for ref in dr_node.imagingStudy or []:
                is_ref = _normalize_reference_target(ref, "ImagingStudy")
                if is_ref and is_ref in imaging_study_map:
                    _add_rel(
                        BasedOnStudyRelation(
                            start_node_id=dr_node.id,
                            end_node_id=imaging_study_map[is_ref].id,
                            test_id=test_id,
                            ActedBy=acted_by,
                        )
                    )

        # 6. (:Observation)-[:SUPPORTS_REPORT]->(:DiagnosticReport)
        for dr_node in diagnostic_report_map.values():
            for ref in dr_node.result or []:
                obs_ref = _normalize_reference_target(ref, "Observation")
                if obs_ref and obs_ref in observation_map:
                    _add_rel(
                        SupportsReportRelation(
                            start_node_id=observation_map[obs_ref].id,
                            end_node_id=dr_node.id,
                            test_id=test_id,
                            ActedBy=acted_by,
                        )
                    )

        # 7. (:Observation)-[:CONTAINS]->(:Observation) via hasMember
        for obs in resources.observations:
            obs_id = getattr(obs, "id", None)
            if not obs_id:
                continue
            parent_key = f"Observation/{obs_id}"
            parent_node = observation_map.get(parent_key)
            if not parent_node:
                continue
            raw_obs = obs.model_dump(mode="json", exclude_none=True)
            for member in raw_obs.get("hasMember") or []:
                member_ref = _normalize_reference_target(_extract_reference(member), "Observation")
                if member_ref and member_ref in observation_map:
                    _add_rel(
                        ContainsRelation(
                            start_node_id=parent_node.id,
                            end_node_id=observation_map[member_ref].id,
                            test_id=test_id,
                            ActedBy=acted_by,
                        )
                    )

        return nodes, relations

    @staticmethod
    def _serialize_node_props(node: FHIRDomainResourceNode) -> dict[str, Any]:
        """Serialize domain properties of a FHIRDomainResourceNode for Cypher SET."""
        dumped = node.model_dump(by_alias=True, exclude_none=True)
        props: dict[str, Any] = {}
        for k, v in dumped.items():
            if k in _AUDIT_INTERNAL_FIELDS:
                continue
            if isinstance(v, datetime):
                props[k] = v.isoformat()
            elif isinstance(v, list) and v and any(isinstance(item, datetime) for item in v):
                props[k] = [item.isoformat() if isinstance(item, datetime) else item for item in v]
            else:
                props[k] = v
        return props

    async def _discover_cross_domain_relations(
        self,
        nodes: Sequence[FHIRDomainResourceNode],
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> list[AuditableRelation]:
        """Find existing DICOM nodes in Neo4j and build Cross-Domain FHIR<->DICOM relations."""
        cross_relations: list[AuditableRelation] = []
        seen_ids: set[str] = set()

        def _add(rel: AuditableRelation) -> None:
            if rel.id not in seen_ids:
                seen_ids.add(rel.id)
                cross_relations.append(rel)

        for node in nodes:
            if isinstance(node, FHIRPatientNode) and node.identifiers:
                records = await self.client.execute_query(
                    """
                    MATCH (p:Patient)
                    WHERE (p.Action IS NULL OR p.Action <> 'DELETE')
                      AND p.patient_id IS NOT NULL
                      AND ('|' + p.patient_id) IN $identifiers
                    RETURN p.id AS id
                    """,
                    {"identifiers": node.identifiers},
                )
                for rec in records:
                    if rec.get("id"):
                        _add(
                            SameAsRelation(
                                start_node_id=rec["id"],
                                end_node_id=node.id,
                                test_id=test_id,
                                ActedBy=acted_by,
                            )
                        )

            elif isinstance(node, ImagingStudyNode):
                if node.studyUid:
                    records = await self.client.execute_query(
                        """
                        MATCH (s:Study {study_instance_uid: $study_uid})
                        WHERE (s.Action IS NULL OR s.Action <> 'DELETE')
                        RETURN s.id AS id
                        """,
                        {"study_uid": node.studyUid},
                    )
                    for rec in records:
                        if rec.get("id"):
                            _add(
                                ReferencesStudyRelation(
                                    start_node_id=node.id,
                                    end_node_id=rec["id"],
                                    test_id=test_id,
                                    ActedBy=acted_by,
                                )
                            )

                if node.seriesUid:
                    records = await self.client.execute_query(
                        """
                        MATCH (se:Series)
                        WHERE se.series_instance_uid IN $series_uids
                          AND (se.Action IS NULL OR se.Action <> 'DELETE')
                        RETURN se.id AS id
                        """,
                        {"series_uids": node.seriesUid},
                    )
                    for rec in records:
                        if rec.get("id"):
                            _add(
                                ReferencesSeriesRelation(
                                    start_node_id=node.id,
                                    end_node_id=rec["id"],
                                    test_id=test_id,
                                    ActedBy=acted_by,
                                )
                            )

                if node.instanceUid:
                    records = await self.client.execute_query(
                        """
                        MATCH (i:Instance)
                        WHERE i.sop_instance_uid IN $instance_uids
                          AND (i.Action IS NULL OR i.Action <> 'DELETE')
                        RETURN i.id AS id
                        """,
                        {"instance_uids": node.instanceUid},
                    )
                    for rec in records:
                        if rec.get("id"):
                            _add(
                                ReferencesInstanceRelation(
                                    start_node_id=node.id,
                                    end_node_id=rec["id"],
                                    test_id=test_id,
                                    ActedBy=acted_by,
                                )
                            )

            elif isinstance(node, ServiceRequestNode) and node.identifiers:
                records = await self.client.execute_query(
                    """
                    MATCH (s:Study)
                    WHERE (s.Action IS NULL OR s.Action <> 'DELETE')
                      AND s.accession_number IS NOT NULL
                      AND s.accession_number <> ''
                      AND ('|' + s.accession_number) IN $identifiers
                    RETURN s.id AS id
                    """,
                    {"identifiers": node.identifiers},
                )
                for rec in records:
                    if rec.get("id"):
                        _add(
                            FulfilledByRelation(
                                start_node_id=node.id,
                                end_node_id=rec["id"],
                                test_id=test_id,
                                ActedBy=acted_by,
                            )
                        )

        return cross_relations

    async def upsert_relevant_resources(
        self,
        resources: EncRelevantResources,
        test_id: Optional[str] = None,
        acted_by: str = "system",
    ) -> dict[str, Any]:
        """Safely upsert all FHIR resources and relationships from EncRelevantResources into Neo4j."""
        await self.graph_service.init_schema_constraints()

        nodes, intra_relations = self.convert_relevant_resources(
            resources=resources,
            test_id=test_id,
            acted_by=acted_by,
        )

        upserted_nodes: list[dict[str, Any]] = []
        for node in nodes:
            label = node.__label__
            props = self._serialize_node_props(node)
            expires_at = node.ExpiresAt.isoformat() if node.ExpiresAt else None
            query = f"""
            MERGE (n:{label} {{id: $id}})
            ON CREATE SET n += $props,
                          n.Action = "CREATE",
                          n.ActedAt = datetime({{timezone: 'UTC'}}),
                          n.ActedBy = $acted_by,
                          n.CreatedAt = datetime({{timezone: 'UTC'}}),
                          n.ExpiresAt = CASE WHEN $expires_at IS NOT NULL THEN datetime($expires_at) ELSE null END
            ON MATCH SET  n += $props,
                          n.Action = "UPDATE",
                          n.ActedAt = datetime({{timezone: 'UTC'}}),
                          n.ActedBy = $acted_by,
                          n.UpdatedAt = datetime({{timezone: 'UTC'}}),
                          n.ExpiresAt = CASE WHEN $expires_at IS NOT NULL
                                             THEN datetime($expires_at)
                                             ELSE n.ExpiresAt END
            RETURN n.id AS id,
                   n.uid AS uid,
                   n.resource_id AS resource_id,
                   n.Action AS action
            """
            records = await self.client.execute_query(
                query,
                {
                    "id": node.id,
                    "props": props,
                    "acted_by": acted_by,
                    "expires_at": expires_at,
                },
            )
            rec = (
                records[0]
                if records
                else {
                    "id": node.id,
                    "uid": node.uid,
                    "resource_id": node.resource_id,
                    "action": "CREATE",
                }
            )
            node_info = {
                "id": rec["id"],
                "label": label,
                "uid": rec["uid"],
                "resource_id": rec["resource_id"],
                "action": rec["action"],
            }
            upserted_nodes.append(node_info)
            logger.info(
                "fhir_node_upserted",
                node_id=node_info["id"],
                label=node_info["label"],
                uid=node_info["uid"],
                resource_id=node_info["resource_id"],
                action=node_info["action"],
            )

        cross_relations = await self._discover_cross_domain_relations(
            nodes=nodes,
            test_id=test_id,
            acted_by=acted_by,
        )
        all_relations = [*intra_relations, *cross_relations]

        upserted_relations: list[dict[str, Any]] = []
        for rel in all_relations:
            rel_type = rel.__type__
            expires_at = rel.ExpiresAt.isoformat() if rel.ExpiresAt else None
            rel_query = f"""
            MATCH (a {{id: $start_node_id}})
            MATCH (b {{id: $end_node_id}})
            MERGE (a)-[r:{rel_type} {{id: $id}}]->(b)
            ON CREATE SET r.test_id = $test_id,
                          r.Action = "CREATE",
                          r.ActedAt = datetime({{timezone: 'UTC'}}),
                          r.ActedBy = $acted_by,
                          r.CreatedAt = datetime({{timezone: 'UTC'}}),
                          r.ExpiresAt = CASE WHEN $expires_at IS NOT NULL THEN datetime($expires_at) ELSE null END
            ON MATCH SET  r.test_id = coalesce($test_id, r.test_id),
                          r.Action = "UPDATE",
                          r.ActedAt = datetime({{timezone: 'UTC'}}),
                          r.ActedBy = $acted_by,
                          r.UpdatedAt = datetime({{timezone: 'UTC'}}),
                          r.ExpiresAt = CASE WHEN $expires_at IS NOT NULL
                                             THEN datetime($expires_at)
                                             ELSE r.ExpiresAt END
            RETURN r.id AS id,
                   type(r) AS type,
                   a.id AS start_node_id,
                   b.id AS end_node_id,
                   r.Action AS action
            """
            records = await self.client.execute_query(
                rel_query,
                {
                    "id": rel.id,
                    "start_node_id": rel.start_node_id,
                    "end_node_id": rel.end_node_id,
                    "test_id": rel.test_id,
                    "acted_by": acted_by,
                    "expires_at": expires_at,
                },
            )
            rec = (
                records[0]
                if records
                else {
                    "id": rel.id,
                    "type": rel_type,
                    "start_node_id": rel.start_node_id,
                    "end_node_id": rel.end_node_id,
                    "action": "CREATE",
                }
            )
            rel_info = {
                "id": rec["id"],
                "type": rec["type"],
                "start_node_id": rec["start_node_id"],
                "end_node_id": rec["end_node_id"],
                "action": rec["action"],
            }
            upserted_relations.append(rel_info)
            logger.info(
                "fhir_relation_upserted",
                relation_id=rel_info["id"],
                type=rel_info["type"],
                start_node_id=rel_info["start_node_id"],
                end_node_id=rel_info["end_node_id"],
                action=rel_info["action"],
            )

        return {
            "nodes": upserted_nodes,
            "relationships": upserted_relations,
            "node_ids": [n["id"] for n in upserted_nodes],
            "relationship_ids": [r["id"] for r in upserted_relations],
        }

    async def cleanup_upserted_resources(
        self,
        test_id: Optional[str] = None,
        node_ids: Optional[Sequence[str]] = None,
        relationship_ids: Optional[Sequence[str]] = None,
    ) -> dict[str, int]:
        """Clean up FHIR nodes and relationships created during testing or ingestion."""
        deleted_rels = 0
        deleted_nodes = 0

        if relationship_ids:
            rel_res = await self.client.execute_query(
                """
                MATCH ()-[r]->()
                WHERE r.id IN $rel_ids
                DELETE r
                RETURN count(r) AS deleted_count
                """,
                {"rel_ids": list(relationship_ids)},
            )
            deleted_rels += int(rel_res[0].get("deleted_count", 0)) if rel_res else 0

        if test_id:
            rel_test_res = await self.client.execute_query(
                """
                MATCH ()-[r {test_id: $test_id}]->()
                DELETE r
                RETURN count(r) AS deleted_count
                """,
                {"test_id": test_id},
            )
            deleted_rels += int(rel_test_res[0].get("deleted_count", 0)) if rel_test_res else 0

        if node_ids:
            node_res = await self.client.execute_query(
                """
                MATCH (n)
                WHERE n.id IN $node_ids
                DETACH DELETE n
                RETURN count(n) AS deleted_count
                """,
                {"node_ids": list(node_ids)},
            )
            deleted_nodes += int(node_res[0].get("deleted_count", 0)) if node_res else 0

        if test_id:
            node_test_res = await self.client.execute_query(
                """
                MATCH (n {test_id: $test_id})
                DETACH DELETE n
                RETURN count(n) AS deleted_count
                """,
                {"test_id": test_id},
            )
            deleted_nodes += int(node_test_res[0].get("deleted_count", 0)) if node_test_res else 0

        logger.info(
            "fhir_resources_cleaned_up",
            test_id=test_id,
            deleted_nodes=deleted_nodes,
            deleted_relationships=deleted_rels,
        )
        return {
            "deleted_nodes": deleted_nodes,
            "deleted_relationships": deleted_rels,
        }


fhir_service = FhirService()
