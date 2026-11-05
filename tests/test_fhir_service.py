"""Unit and integration tests for FHIR service and batch fetcher."""

import httpx
import pytest
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.patient import Patient

from core.config import get_settings
from service.fhir_service import (
    EncounterContext,
    EncRelevantResources,
    FHIRBatchFetcher,
    FhirService,
)


def _is_fhir_proxy_live() -> bool:
    """Check if the local FHIR proxy server is actively listening and responsive."""
    settings = get_settings()
    base_url = settings.FHIR_BASE_URL.rstrip("/")
    if not base_url:
        return False
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{base_url}/metadata")
            return resp.status_code == 200
    except Exception:
        return False


FHIR_PROXY_LIVE = _is_fhir_proxy_live()


# =====================================================================
# Unit Tests: Models
# =====================================================================


def test_encounter_context_model():
    """Verify EncounterContext initialization, defaults, and error accumulation."""
    ctx = EncounterContext(
        encounter={"id": "enc-101", "resourceType": "Encounter"},
        conditions=[{"id": "c-1"}],
        errors=["Failed to fetch observations"],
    )
    assert ctx.encounter["id"] == "enc-101"
    assert len(ctx.conditions) == 1
    assert ctx.diagnostic_reports == []
    assert ctx.imaging_studies == []
    assert ctx.observations == []
    assert ctx.procedures == []
    assert ctx.patient is None
    assert ctx.errors == ["Failed to fetch observations"]


def test_enc_relevant_resources_model():
    """Verify EncRelevantResources initialization with typed FHIR models."""
    enc = Encounter(
        **{
            "status": "finished",
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "AMB",
            },
            "id": "enc-1",
        }
    )
    pat = Patient(id="pat-1")
    cond = Condition(
        clinicalStatus={
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": "active",
                }
            ]
        },
        subject={"reference": "Patient/pat-1"},
        id="c-1",
    )

    resources = EncRelevantResources(
        encounter=enc,
        patient=pat,
        conditions=[cond],
    )
    assert resources.encounter is not None
    assert resources.encounter.id == "enc-1"
    assert resources.patient is not None
    assert resources.patient.id == "pat-1"
    assert len(resources.conditions) == 1
    assert resources.diagnostic_reports == []
    assert resources.imaging_studies == []
    assert resources.observations == []
    assert resources.procedures == []


# =====================================================================
# Unit Tests: FHIRBatchFetcher (Mocked transport)
# =====================================================================


@pytest.mark.anyio
async def test_fetch_resource_bundle_mocked():
    """Verify FHIRBatchFetcher correctly extracts resources from a Bundle payload."""
    bundle_payload = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "DiagnosticReport",
                    "id": "dr-1",
                    "status": "final",
                    "code": {"text": "Test Report"},
                }
            },
            {
                "resource": {
                    "resourceType": "DiagnosticReport",
                    "id": "dr-2",
                    "status": "final",
                    "code": {"text": "Second Report"},
                }
            },
            {"other": "missing resource"},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fhir/r4/DiagnosticReport"
        assert request.url.params["encounter"] == "enc-123"
        return httpx.Response(200, json=bundle_payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        results = await fetcher.fetch_resource_bundle("DiagnosticReport", {"encounter": "enc-123"})
        assert len(results) == 2
        assert results[0]["id"] == "dr-1"
        assert results[1]["id"] == "dr-2"


@pytest.mark.anyio
async def test_fetch_resource_bundle_unconfigured_or_404():
    """Verify handling when base_url is unconfigured or search returns 404."""
    # 1. Unconfigured base_url
    empty_fetcher = FHIRBatchFetcher(base_url="")
    res = await empty_fetcher.fetch_resource_bundle("Patient", {})
    assert res == []

    # 2. 404 returns empty list
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"resourceType": "OperationOutcome"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        results = await fetcher.fetch_resource_bundle("NonExistent", {})
        assert results == []


@pytest.mark.anyio
async def test_fetch_by_reference_and_fetch_resource_mocked():
    """Verify single resource retrieval by relative reference, direct URL, and resource ID."""
    pat_payload = {"resourceType": "Patient", "id": "pat-999"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fhir/r4/Patient/pat-999":
            return httpx.Response(200, json=pat_payload)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)

        # 1. Fetch by relative reference
        data = await fetcher.fetch_by_reference("Patient/pat-999")
        assert data is not None
        assert data["id"] == "pat-999"

        # 2. Fetch by full URL
        data2 = await fetcher.fetch_by_reference("http://mock-fhir/fhir/r4/Patient/pat-999")
        assert data2 is not None
        assert data2["id"] == "pat-999"

        # 3. Fetch via fetch_resource helper
        data3 = await fetcher.fetch_resource("Patient", "pat-999")
        assert data3 is not None
        assert data3["id"] == "pat-999"

        # 4. Non-existent resource returns None
        data4 = await fetcher.fetch_by_reference("Patient/missing")
        assert data4 is None

        # 5. Empty reference returns None
        assert await fetcher.fetch_by_reference("") is None


@pytest.mark.anyio
async def test_fetcher_context_manager():
    """Verify FHIRBatchFetcher can be used as an async context manager."""
    async with FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4") as fetcher:
        assert fetcher.client is not None
        assert not fetcher.client.is_closed
    # After context exit, client is closed
    assert fetcher._client is None


@pytest.mark.anyio
async def test_fetch_encounter_graph_mocked_partial_failure():
    """Verify that if one fanned-out request fails, others succeed and the error is captured."""
    enc = Encounter(
        **{
            "id": "enc-555",
            "status": "finished",
            "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                "code": "AMB",
            },
            "subject": {"reference": "Patient/pat-555"},
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/Patient/pat-555" in path:
            return httpx.Response(200, json={"resourceType": "Patient", "id": "pat-555"})
        elif "/DiagnosticReport" in path:
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "DiagnosticReport",
                                "id": "dr-5",
                                "status": "final",
                                "code": {"text": "Note"},
                            }
                        }
                    ],
                },
            )
        elif "/Observation" in path:
            # Simulate a 500 server error on observation endpoint
            return httpx.Response(500, json={"error": "Database connection failed"})
        else:
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        ctx = await fetcher.fetch_encounter_graph(encounter=enc)

        assert ctx.encounter["id"] == "enc-555"
        assert ctx.patient is not None
        assert ctx.patient["id"] == "pat-555"
        assert len(ctx.diagnostic_reports) == 1
        assert ctx.diagnostic_reports[0]["id"] == "dr-5"
        assert ctx.observations == []
        assert any("Failed to fetch observations" in err for err in ctx.errors)


@pytest.mark.anyio
async def test_fetch_encounter_graph_invalid_input():
    """Verify fetch_encounter_graph validation on encounter without id or unsupported type."""
    fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4")

    # Encounter dict missing id
    with pytest.raises(ValueError, match="Encounter must have a valid 'id'"):
        await fetcher.fetch_encounter_graph({"resourceType": "Encounter"})

    # Unsupported type
    with pytest.raises(ValueError, match="Unsupported encounter type"):
        await fetcher.fetch_encounter_graph(12345)  # type: ignore


# =====================================================================
# Unit Tests: FhirService (Mocked fetcher)
# =====================================================================


@pytest.mark.anyio
async def test_fhir_service_fetch_imaging_study_mocked():
    """Verify FhirService.fetch_imaging_study query resolution and fallback."""
    raw_study = {
        "resourceType": "ImagingStudy",
        "id": "study-123",
        "status": "available",
        "subject": {"reference": "Patient/p-1"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "identifier" in params and "1.2.3.4.5" in params["identifier"]:
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "entry": [{"resource": raw_study}],
                },
            )
        elif "_id" in params and params["_id"] == "fallback-id":
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "entry": [{"resource": raw_study}],
                },
            )
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        svc = FhirService(fetcher=fetcher)

        # Test search with bare UID
        study = await svc.fetch_imaging_study("1.2.3.4.5")
        assert study is not None
        assert study.id == "study-123"

        # Test search with urn:oid: prefix
        study2 = await svc.fetch_imaging_study("urn:oid:1.2.3.4.5")
        assert study2 is not None
        assert study2.id == "study-123"

        # Test fallback to _id
        study_fallback = await svc.fetch_imaging_study("fallback-id")
        assert study_fallback is not None
        assert study_fallback.id == "study-123"

        # Test missing study
        study3 = await svc.fetch_imaging_study("9.9.9.9")
        assert study3 is None

        # Test empty string
        assert await svc.fetch_imaging_study("") is None


@pytest.mark.anyio
async def test_fhir_service_fetch_encounter_and_patient_mocked():
    """Verify fetch_encounter and fetch_patient with mocks."""
    raw_enc = {
        "resourceType": "Encounter",
        "id": "enc-1",
        "status": "in-progress",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "IMP",
        },
    }
    raw_pat = {"resourceType": "Patient", "id": "pat-1"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/Encounter" in path and request.url.params.get("_id") == "enc-1":
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": raw_enc}]})
        elif "/Patient/pat-1" in path:
            return httpx.Response(200, json=raw_pat)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        svc = FhirService(fetcher=fetcher)

        # 1. Fetch encounter
        enc = await svc.fetch_encounter("enc-1")
        assert enc is not None
        assert enc.id == "enc-1"

        # 2. Fetch encounter non-existent
        assert await svc.fetch_encounter("missing") is None
        assert await svc.fetch_encounter("") is None

        # 3. Fetch patient
        pat = await svc.fetch_patient("Patient/pat-1")
        assert pat is not None
        assert pat.id == "pat-1"
        assert await svc.fetch_patient("") is None


@pytest.mark.anyio
async def test_fhir_service_diagnostic_reports_mocked():
    """Verify fetch_enc_diagnostic_reports and fetch_enc_diagnostic_report."""
    dr1 = {
        "resourceType": "DiagnosticReport",
        "id": "dr-primary",
        "status": "final",
        "code": {"text": "Chest X-Ray"},
    }
    dr2 = {
        "resourceType": "DiagnosticReport",
        "id": "dr-secondary",
        "status": "final",
        "code": {"text": "CT Abdomen"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("encounter") == "enc-multi":
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "entry": [{"resource": dr1}, {"resource": dr2}],
                },
            )
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        svc = FhirService(fetcher=fetcher)

        # Multiple reports
        reports = await svc.fetch_enc_diagnostic_reports("enc-multi")
        assert len(reports) == 2
        assert reports[0].id == "dr-primary"
        assert reports[1].id == "dr-secondary"

        # Primary single report
        primary = await svc.fetch_enc_diagnostic_report("enc-multi")
        assert primary is not None
        assert primary.id == "dr-primary"

        # Non-existent
        assert await svc.fetch_enc_diagnostic_report("enc-none") is None
        assert await svc.fetch_enc_diagnostic_reports("") == []


@pytest.mark.anyio
async def test_fhir_service_fetch_enc_relevant_resources_by_str_id():
    """Verify fetch_enc_relevant_resources seamlessly resolves an encounter string ID."""
    raw_enc = {
        "resourceType": "Encounter",
        "id": "enc-900",
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
        },
        "subject": {"reference": "Patient/pat-900"},
    }
    raw_pat = {"resourceType": "Patient", "id": "pat-900"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/Encounter" in path and request.url.params.get("_id") == "enc-900":
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "entry": [{"resource": raw_enc}],
                },
            )
        elif "/Patient/pat-900" in path:
            return httpx.Response(200, json=raw_pat)
        else:
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        svc = FhirService(fetcher=fetcher)

        # Pass string ID instead of Encounter object
        res = await svc.fetch_enc_relevant_resources("enc-900")
        assert res.encounter is not None
        assert res.encounter.id == "enc-900"
        assert res.patient is not None
        assert res.patient.id == "pat-900"

        # Pass non-existent string ID
        with pytest.raises(ValueError, match="Encounter not found for ID: missing-enc"):
            await svc.fetch_enc_relevant_resources("missing-enc")


@pytest.mark.anyio
async def test_fhir_service_fetch_imaging_studies_mocked():
    """Verify fetch_imaging_studies returns all matched imaging studies."""
    raw_study1 = {
        "resourceType": "ImagingStudy",
        "id": "study-1",
        "status": "available",
        "subject": {"reference": "Patient/p-1"},
    }
    raw_study2 = {
        "resourceType": "ImagingStudy",
        "id": "study-2",
        "status": "available",
        "subject": {"reference": "Patient/p-1"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if "identifier" in params and "1.2.3.4" in params["identifier"]:
            return httpx.Response(
                200,
                json={
                    "resourceType": "Bundle",
                    "entry": [{"resource": raw_study1}, {"resource": raw_study2}],
                },
            )
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        svc = FhirService(fetcher=fetcher)

        studies = await svc.fetch_imaging_studies("1.2.3.4")
        assert len(studies) == 2
        assert studies[0].id == "study-1"
        assert studies[1].id == "study-2"

        # fetch_imaging_study returns first
        study = await svc.fetch_imaging_study("1.2.3.4")
        assert study is not None
        assert study.id == "study-1"

        # Empty UID returns empty list
        assert await svc.fetch_imaging_studies("") == []


@pytest.mark.anyio
async def test_fetch_relevant_resources_of_study_mocked():
    """Verify fetch_relevant_resources_of_study error paths and complete graph resolution."""
    raw_study = {
        "resourceType": "ImagingStudy",
        "id": "study-100",
        "status": "available",
        "subject": {"reference": "Patient/pat-100"},
        "encounter": {"reference": "Encounter/enc-100"},
    }
    raw_enc = {
        "resourceType": "Encounter",
        "id": "enc-100",
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
        },
        "subject": {"reference": "Patient/pat-100"},
    }
    raw_pat = {"resourceType": "Patient", "id": "pat-100"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        if "/ImagingStudy" in path and "1.2.3.99" in params.get("identifier", ""):
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": raw_study}]})
        elif "/ImagingStudy" in path and "no-enc" in params.get("identifier", ""):
            no_enc_study = dict(raw_study)
            no_enc_study.pop("encounter", None)
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": no_enc_study}]})
        elif "/ImagingStudy" in path and "missing-enc" in params.get("identifier", ""):
            miss_enc_study = dict(raw_study)
            miss_enc_study["encounter"] = {"reference": "Encounter/nonexistent"}
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": miss_enc_study}]})
        elif "/Encounter" in path and params.get("_id") == "enc-100":
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": raw_enc}]})
        elif "/Patient/pat-100" in path:
            return httpx.Response(200, json=raw_pat)
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        svc = FhirService(fetcher=fetcher)

        # 1. Empty study_uid
        empty_res = await svc.fetch_relevant_resources_of_study("")
        assert "empty study_uid" in empty_res.errors[0]

        # 2. Study not found
        not_found_res = await svc.fetch_relevant_resources_of_study("not-found")
        assert "failed_to_fetch_imaging_study" in not_found_res.errors[0]

        # 3. Study found but missing encounter reference
        no_enc_res = await svc.fetch_relevant_resources_of_study("no-enc")
        assert any("failed_to_fetch_encounter ref" in e for e in no_enc_res.errors)

        # 4. Encounter reference present but encounter resource missing
        miss_enc_res = await svc.fetch_relevant_resources_of_study("missing-enc")
        assert any("failed_to_fetch_encounter: nonexistent" in e for e in miss_enc_res.errors)

        # 5. Full successful resolution
        full_res = await svc.fetch_relevant_resources_of_study("1.2.3.99")
        assert full_res.encounter is not None
        assert full_res.encounter.id == "enc-100"
        assert full_res.patient is not None
        assert full_res.patient.id == "pat-100"
        assert len(full_res.imaging_studies) >= 1
        assert full_res.imaging_studies[0].id == "study-100"
        assert full_res.errors == []


# =====================================================================
# Live Integration Tests (against running FHIR proxy)
# =====================================================================


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_encounter():
    """Fetch live encounter 3033 from HAPI FHIR proxy and validate fields."""
    svc = FhirService()
    enc = await svc.fetch_encounter("3033")
    assert enc is not None
    assert enc.id == "3033"
    assert enc.status == "finished"
    assert enc.subject is not None
    assert enc.subject.reference == "Patient/2977"


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_patient():
    """Fetch live Patient 2977 from HAPI FHIR proxy."""
    svc = FhirService()
    patient = await svc.fetch_patient("Patient/2977")
    assert patient is not None
    assert patient.id == "2977"


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_imaging_study_by_uid():
    """Fetch live ImagingStudy using DICOM Study Instance UID and urn:oid prefix."""
    svc = FhirService()
    study_uid = "1.2.840.99999999.89216092.1553055938354"

    # 1. Bare UID
    study = await svc.fetch_imaging_study(study_uid)
    assert study is not None
    assert study.id == "3051"
    assert study.status == "available"

    # 2. With urn:oid: prefix
    study_urn = await svc.fetch_imaging_study(f"urn:oid:{study_uid}")
    assert study_urn is not None
    assert study_urn.id == "3051"


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_diagnostic_report():
    """Fetch live DiagnosticReport for encounter 3033."""
    svc = FhirService()
    report = await svc.fetch_enc_diagnostic_report("3033")
    assert report is not None
    assert report.id == "3052"
    assert report.status == "final"
    assert report.encounter is not None
    assert report.encounter.reference == "Encounter/3033"


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_enc_relevant_resources_3033():
    """Fetch entire encounter 3033 resource graph concurrently from proxy."""
    svc = FhirService()
    resources = await svc.fetch_enc_relevant_resources("3033")

    assert resources.encounter is not None
    assert resources.encounter.id == "3033"
    assert resources.patient is not None
    assert resources.patient.id == "2977"

    assert len(resources.diagnostic_reports) >= 1
    assert resources.diagnostic_reports[0].id == "3052"

    assert len(resources.imaging_studies) >= 1
    assert resources.imaging_studies[0].id == "3051"

    assert len(resources.procedures) >= 1
    assert resources.errors == []


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_enc_relevant_resources_12144():
    """Fetch encounter 12144 with observations, conditions, and reports."""
    svc = FhirService()
    resources = await svc.fetch_enc_relevant_resources("12144")

    assert resources.encounter is not None
    assert resources.encounter.id == "12144"
    assert len(resources.observations) > 0
    assert len(resources.conditions) > 0
    assert len(resources.diagnostic_reports) > 0
    assert len(resources.procedures) > 0
    assert resources.errors == []
