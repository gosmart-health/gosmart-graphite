"""FHIR integration service and batch fetcher for DICOM-FHIR graph server."""

from __future__ import annotations

import asyncio
from typing import Any, Optional, Union

import httpx
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.imagingstudy import ImagingStudy
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.procedure import Procedure
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.servicerequest import ServiceRequest
from pydantic import BaseModel, ConfigDict, Field

from core.config import get_settings
from core.logging import get_logger

logger = get_logger("gosmart_gs.fhir_service")


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

    def __init__(self, fetcher: Optional[FHIRBatchFetcher] = None) -> None:
        self.fetcher: FHIRBatchFetcher = fetcher if fetcher is not None else FHIRBatchFetcher()

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

    async def fetch_relevant_resources_of_study(
        self, study_uid: str
    ) -> EncRelevantResources:
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
