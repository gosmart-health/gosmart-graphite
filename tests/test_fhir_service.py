"""Unit and integration tests for FHIR service, batch fetcher, and Neo4j graph upsert."""

from typing import Any, Optional

import httpx
import pytest
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.imagingstudy import ImagingStudy
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.procedure import Procedure
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.servicerequest import ServiceRequest

from core.config import get_settings
from db.neo4j import neo4j_client
from model.dicom import DicomHierarchy, InstanceModel, PatientModel, SeriesModel, StudyModel
from service.fhir_service import (
    EncounterContext,
    EncRelevantResources,
    FHIRBatchFetcher,
    FhirService,
)
from service.graph_service import graph_service
from tests.utils.config_utils import ConfigUtils

config_utils = ConfigUtils()
all_configs = config_utils.configs

# Set "cleanup_enable": false in tests/configs.json to temporarily disable post-test
# cleanup so that upserted FHIR nodes and relationships can be inspected in Neo4j.


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
    except Exception as why:
        print(why)
        return False


FHIR_PROXY_LIVE = _is_fhir_proxy_live()


async def cleanup_fhir_test_graph(
    svc: FhirService,
    test_id: Optional[str] = None,
    node_ids: Optional[list[str]] = None,
    relationship_ids: Optional[list[str]] = None,
    enabled: bool = all_configs["cleanup_enable"],
) -> dict[str, int]:
    """Separate disposal helper to clean up FHIR nodes and relationships created by a test.

    Can be temporarily disabled by setting ``"cleanup_enable": false`` in ``tests/configs.json``
    to inspect the graph in Neo4j after test runs.
    """
    if not enabled:
        return {"deleted_nodes": 0, "deleted_relationships": 0}
    return await svc.cleanup_upserted_resources(
        test_id=test_id,
        node_ids=node_ids,
        relationship_ids=relationship_ids,
    )


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
    assert isinstance(ctx.encounter, dict)
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
        clinicalStatus=CodeableConcept.model_validate(
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                        "code": "active",
                    }
                ]
            }
        ),
        subject=Reference.model_validate({"reference": "Patient/pat-1"}),
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


def test_convert_relevant_resources_and_relations():
    """Verify conversion of all 8 FHIR resource types into graph nodes and intra-FHIR relationships."""
    pat = Patient.model_validate(
        {
            "resourceType": "Patient",
            "id": "pat-10",
            "active": True,
            "gender": "female",
            "birthDate": "1980-05-15",
            "identifier": [{"system": "http://hospital.example.org/mrn", "value": "MRN-10"}],
            "name": [{"prefix": ["Ms."], "given": ["Jane"], "family": "Doe"}],
        }
    )
    enc = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-10",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
            "subject": {"reference": "Patient/pat-10"},
        }
    )
    cond = Condition.model_validate(
        {
            "resourceType": "Condition",
            "id": "cond-10",
            "clinicalStatus": {
                "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
            },
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "399261000", "display": "History"}]},
            "subject": {"reference": "Patient/pat-10"},
            "encounter": {"reference": "Encounter/enc-10"},
        }
    )
    sr = ServiceRequest.model_validate(
        {
            "resourceType": "ServiceRequest",
            "id": "sr-10",
            "status": "active",
            "intent": "order",
            "identifier": [{"system": "http://hospital.example.org/acc", "value": "ACC-10"}],
            "code": {"coding": [{"system": "http://loinc.org", "code": "24627-2"}], "text": "CT Chest"},
            "subject": {"reference": "Patient/pat-10"},
            "encounter": {"reference": "Encounter/enc-10"},
        }
    )
    img = ImagingStudy.model_validate(
        {
            "resourceType": "ImagingStudy",
            "id": "is-10",
            "status": "available",
            "identifier": [{"system": "urn:ietf:rfc:3986", "value": "urn:oid:1.2.840.10008.10"}],
            "subject": {"reference": "Patient/pat-10"},
            "encounter": {"reference": "Encounter/enc-10"},
            "basedOn": [{"reference": "ServiceRequest/sr-10"}],
            "series": [
                {
                    "uid": "1.2.840.10008.10.1",
                    "modality": {"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "CR"},
                    "instance": [
                        {
                            "uid": "1.2.840.10008.10.1.1",
                            "sopClass": {"system": "urn:ietf:rfc:3986", "code": "urn:oid:1.2.840.10008.5.1.4.1.1.1"},
                        }
                    ],
                }
            ],
        }
    )
    obs_parent = Observation.model_validate(
        {
            "resourceType": "Observation",
            "id": "obs-parent-10",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure"}]},
            "subject": {"reference": "Patient/pat-10"},
            "encounter": {"reference": "Encounter/enc-10"},
            "basedOn": [{"reference": "ServiceRequest/sr-10"}],
            "hasMember": [{"reference": "Observation/obs-child-10"}],
        }
    )
    obs_child = Observation.model_validate(
        {
            "resourceType": "Observation",
            "id": "obs-child-10",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6", "display": "Systolic BP"}]},
            "subject": {"reference": "Patient/pat-10"},
            "encounter": {"reference": "Encounter/enc-10"},
            "valueQuantity": {"value": 120.0, "unit": "mmHg", "system": "http://unitsofmeasure.org", "code": "mm[Hg]"},
        }
    )
    dr = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "dr-10",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "24627-2"}]},
            "subject": {"reference": "Patient/pat-10"},
            "encounter": {"reference": "Encounter/enc-10"},
            "basedOn": [{"reference": "ServiceRequest/sr-10"}],
            "imagingStudy": [{"reference": "ImagingStudy/is-10"}],
            "result": [{"reference": "Observation/obs-parent-10"}, {"reference": "Observation/obs-child-10"}],
        }
    )
    proc = Procedure.model_validate(
        {
            "resourceType": "Procedure",
            "id": "proc-10",
            "status": "completed",
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "399208008"}]},
            "subject": {"reference": "Patient/pat-10"},
            "encounter": {"reference": "Encounter/enc-10"},
            "basedOn": [{"reference": "ServiceRequest/sr-10"}],
        }
    )

    bundle = EncRelevantResources(
        patient=pat,
        encounter=enc,
        conditions=[cond],
        serviceRequests=[sr],
        imaging_studies=[img],
        observations=[obs_parent, obs_child],
        diagnostic_reports=[dr],
        procedures=[proc],
    )

    svc = FhirService()
    nodes, relations = svc.convert_relevant_resources(bundle, test_id="unit_test_conv", acted_by="tester")

    assert len(nodes) == 9
    rel_types = {r.__type__ for r in relations}
    assert {
        "SUBJECT",
        "PERFORMED_IN",
        "HAS_ENCOUNTER",
        "BASED_ON",
        "BASED_ON_STUDY",
        "SUPPORTS_REPORT",
        "CONTAINS",
    }.issubset(rel_types)


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
    def handler(_: httpx.Request) -> httpx.Response:
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

        assert isinstance(ctx.encounter, dict)
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
# Neo4j Upsert & Cleanup Integration Tests
# =====================================================================


@pytest.mark.anyio
async def test_upsert_relevant_resources_with_cross_domain_and_cleanup_toggle():
    """Verify FHIR upsert (CREATE then UPDATE), cross-domain DICOM linking, and cleanup toggle."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    test_id = "test_fhir_cross_domain_upsert"
    svc = FhirService()

    # Clean up any leftover data before the test starts
    await svc.cleanup_upserted_resources(test_id=test_id)
    await graph_service.cleanup_test_data(test_id)

    # Seed a DICOM hierarchy so cross-domain links (:SAME_AS, :REFERENCES_*, :FULFILLED_BY) are tested
    dicom_hierarchy = DicomHierarchy(
        patient=PatientModel(patient_id="MRN-CROSS-001", patient_name="CROSS^JANE"),
        study=StudyModel(
            study_instance_uid="1.2.840.99999999.777.1",
            accession_number="ACC-CROSS-001",
            study_description="Chest X-Ray",
        ),
        series=SeriesModel(
            series_instance_uid="1.2.840.99999999.777.1.1",
            modality="CR",
        ),
        instance=InstanceModel(
            sop_instance_uid="1.2.840.99999999.777.1.1.1",
            sop_class_uid="1.2.840.10008.5.1.4.1.1.1",
        ),
        test_id=test_id,
        acted_by="pytest",
    )
    await graph_service.save_hierarchy(dicom_hierarchy)

    resources = EncRelevantResources(
        patient=Patient.model_validate(
            {
                "resourceType": "Patient",
                "id": "pat-cross-1",
                "active": True,
                "identifier": [{"system": "http://hospital.example.org/mrn", "value": "MRN-CROSS-001"}],
                "name": [{"given": ["Jane"], "family": "Cross"}],
            }
        ),
        encounter=Encounter.model_validate(
            {
                "resourceType": "Encounter",
                "id": "enc-cross-1",
                "status": "finished",
                "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
                "subject": {"reference": "Patient/pat-cross-1"},
            }
        ),
        serviceRequests=[
            ServiceRequest.model_validate(
                {
                    "resourceType": "ServiceRequest",
                    "id": "sr-cross-1",
                    "status": "active",
                    "intent": "order",
                    "identifier": [{"system": "http://hospital.example.org/acc", "value": "ACC-CROSS-001"}],
                    "code": {"coding": [{"system": "http://loinc.org", "code": "24627-2"}]},
                    "subject": {"reference": "Patient/pat-cross-1"},
                    "encounter": {"reference": "Encounter/enc-cross-1"},
                }
            )
        ],
        imaging_studies=[
            ImagingStudy.model_validate(
                {
                    "resourceType": "ImagingStudy",
                    "id": "is-cross-1",
                    "status": "available",
                    "identifier": [{"system": "urn:ietf:rfc:3986", "value": "urn:oid:1.2.840.99999999.777.1"}],
                    "subject": {"reference": "Patient/pat-cross-1"},
                    "encounter": {"reference": "Encounter/enc-cross-1"},
                    "basedOn": [{"reference": "ServiceRequest/sr-cross-1"}],
                    "series": [
                        {
                            "uid": "1.2.840.99999999.777.1.1",
                            "modality": {"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "CR"},
                            "instance": [
                                {
                                    "uid": "1.2.840.99999999.777.1.1.1",
                                    "sopClass": {
                                        "system": "urn:ietf:rfc:3986",
                                        "code": "urn:oid:1.2.840.10008.5.1.4.1.1.1",
                                    },
                                }
                            ],
                        }
                    ],
                }
            )
        ],
    )

    try:
        # Pass 1: INSERT (CREATE)
        res1 = await svc.upsert_relevant_resources(resources, test_id=test_id, acted_by="pytest_pass1")
        assert len(res1["nodes"]) == 4
        assert all(n["action"] == "CREATE" for n in res1["nodes"])
        assert all(r["action"] == "CREATE" for r in res1["relationships"])

        rel_types = {r["type"] for r in res1["relationships"]}
        assert {
            "SUBJECT",
            "PERFORMED_IN",
            "BASED_ON",
            "SAME_AS",
            "REFERENCES_STUDY",
            "REFERENCES_SERIES",
            "REFERENCES_INSTANCE",
            "FULFILLED_BY",
        }.issubset(rel_types)

        # Pass 2: UPDATE
        res2 = await svc.upsert_relevant_resources(resources, test_id=test_id, acted_by="pytest_pass2")
        assert res2["node_ids"] == res1["node_ids"]
        assert res2["relationship_ids"] == res1["relationship_ids"]
        assert all(n["action"] == "UPDATE" for n in res2["nodes"])
        assert all(r["action"] == "UPDATE" for r in res2["relationships"])

        # Verify cleanup disabled leaves nodes in DB
        no_op = await cleanup_fhir_test_graph(
            svc,
            test_id=test_id,
            node_ids=res2["node_ids"],
            relationship_ids=res2["relationship_ids"],
            enabled=False,
        )
        assert no_op["deleted_nodes"] == 0

        # Verify cleanup enabled removes created FHIR nodes and relationships
        cleaned = await cleanup_fhir_test_graph(
            svc,
            test_id=test_id,
            node_ids=res2["node_ids"],
            relationship_ids=res2["relationship_ids"],
            enabled=True,
        )
        assert cleaned["deleted_nodes"] >= 4
        assert cleaned["deleted_relationships"] >= len(res2["relationship_ids"])
    finally:
        await graph_service.cleanup_test_data(test_id)


# =====================================================================
# Live Integration Tests (against running FHIR proxy)
# =====================================================================


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_encounter(test_config: dict[str, Any]):
    """
    Fetch live encounter 3033 from HAPI FHIR proxy and validate fields.
    Please see tests/configs.json for test_config values and conftest.py where
    the pytest fixture "test_config" is defined..
    IMPORTANT:
    IF YOU GENERATE new HAPI FHIR records you need to find sutiable
    Live configurations.
    """
    svc = FhirService()
    enc = await svc.fetch_encounter(test_config["encounter.id"])
    assert enc is not None
    assert enc.id == test_config["encounter.id"]
    assert enc.status == test_config["encounter.status"]
    assert enc.subject is not None
    assert enc.subject.reference == test_config["subject.reference"]


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_patient(test_config: dict[str, Any]):
    """Fetch live Patient 2977 from HAPI FHIR proxy."""
    svc = FhirService()
    patient = await svc.fetch_patient(test_config["subject.reference"])
    assert patient is not None
    assert patient.id == test_config["subject.id"]


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_imaging_study_by_uid(test_config: dict[str, Any]):
    """Fetch live ImagingStudy using DICOM Study Instance UID and urn:oid prefix."""
    svc = FhirService()
    study_uid = test_config["study_uid"]

    # 1. Bare UID
    study = await svc.fetch_imaging_study(study_uid)
    assert study is not None
    assert study.id == test_config["study.id"]
    assert study.status == test_config["study.status"]

    # 2. With urn:oid: prefix
    study_urn = await svc.fetch_imaging_study(f"urn:oid:{study_uid}")
    assert study_urn is not None
    assert study_urn.id == test_config["study.id"]


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_diagnostic_report(test_config: dict[str, Any]):
    """Fetch live DiagnosticReport for encounter 3033."""
    svc = FhirService()
    report = await svc.fetch_enc_diagnostic_report(test_config["encounter.id"])
    assert report is not None
    assert report.id == test_config["report.id"]
    assert report.status == test_config["report.status"]
    assert report.encounter is not None
    assert report.encounter.reference == test_config["encounter.reference"]


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_enc_relevant_resources(test_config: dict[str, Any]):
    """Fetch entire encounter 3033 resource graph concurrently from proxy."""
    svc = FhirService()
    resources = await svc.fetch_enc_relevant_resources(test_config["encounter.id"])

    assert resources.encounter is not None
    assert resources.encounter.id == test_config["encounter.id"]
    assert resources.patient is not None
    assert resources.patient.id == test_config["patient.id"]

    assert ConfigUtils.compare(len(resources.diagnostic_reports), test_config["diagnostic.reports.length"])
    assert resources.diagnostic_reports[0].id == test_config["diagnostic.reports.id_0"]

    assert ConfigUtils.compare(len(resources.imaging_studies), test_config["imaging_studies.length"])
    assert resources.imaging_studies[0].id == test_config["imaging_studies.id_0"]

    assert ConfigUtils.compare(len(resources.procedures), test_config["procedures.length"])
    assert ConfigUtils.compare(len(resources.errors), test_config["errors.length"])


@pytest.mark.skipif(not FHIR_PROXY_LIVE, reason="Live FHIR proxy is not running")
@pytest.mark.anyio
async def test_live_fetch_and_upsert_study_790735190745_twice(test_config: dict[str, Any]):
    """Fetch live study 1.2.840.99999999.40196652.790735190745 and upsert twice (CREATE then UPDATE)."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    test_id = "test_fhir_study_790735190745"
    fhir = FhirService()

    # Ensure clean slate prior to test run
    await fhir.cleanup_upserted_resources(test_id=test_id)

    resources = await fhir.fetch_relevant_resources_of_study(test_config["study_uid"])
    assert resources.encounter is not None
    assert resources.patient is not None
    assert ConfigUtils.compare(len(resources.imaging_studies), test_config["imaging_studies.length"])
    assert ConfigUtils.compare(len(resources.diagnostic_reports), test_config["diagnostic_reports.length"])
    assert ConfigUtils.compare(len(resources.observations), test_config["observations.length"])
    assert ConfigUtils.compare(len(resources.procedures), test_config["procedures.length"])

    upserted_node_ids: list[str] = []
    upserted_rel_ids: list[str] = []

    try:
        # 1. First pass: Inserting (Action == "CREATE")
        first_result = await fhir.upsert_relevant_resources(
            resources,
            test_id=test_id,
            acted_by="pytest_first_pass",
        )
        upserted_node_ids = first_result["node_ids"]
        upserted_rel_ids = first_result["relationship_ids"]

        assert len(upserted_node_ids) > 0
        assert len(upserted_rel_ids) > 0
        assert all(n["action"] == "CREATE" for n in first_result["nodes"])
        assert all(r["action"] == "CREATE" for r in first_result["relationships"])

        # Verify nodes exist in Neo4j with Action == "CREATE" and CreatedAt set
        db_create_check = await neo4j_client.execute_query(
            """
            MATCH (n {test_id: $test_id})
            RETURN count(n) AS cnt,
                   collect(distinct n.Action) AS actions,
                   count(n.CreatedAt) AS created_cnt
            """,
            {"test_id": test_id},
        )
        assert db_create_check[0]["cnt"] == len(upserted_node_ids)
        assert db_create_check[0]["actions"] == ["CREATE"]
        assert db_create_check[0]["created_cnt"] == len(upserted_node_ids)

        # 2. Second pass: Updating (Action == "UPDATE")
        second_result = await fhir.upsert_relevant_resources(
            resources,
            test_id=test_id,
            acted_by="pytest_second_pass",
        )
        assert second_result["node_ids"] == upserted_node_ids
        assert second_result["relationship_ids"] == upserted_rel_ids
        assert all(n["action"] == "UPDATE" for n in second_result["nodes"])
        assert all(r["action"] == "UPDATE" for r in second_result["relationships"])

        # Verify in Neo4j that no duplicates were created and Action == "UPDATE", UpdatedAt set
        db_update_check = await neo4j_client.execute_query(
            """
            MATCH (n {test_id: $test_id})
            RETURN count(n) AS cnt,
                   collect(distinct n.Action) AS actions,
                   count(n.UpdatedAt) AS updated_cnt
            """,
            {"test_id": test_id},
        )
        assert db_update_check[0]["cnt"] == len(upserted_node_ids)
        assert db_update_check[0]["actions"] == ["UPDATE"]
        assert db_update_check[0]["updated_cnt"] == len(upserted_node_ids)

        rel_update_check = await neo4j_client.execute_query(
            """
            MATCH ()-[r {test_id: $test_id}]->()
            RETURN count(r) AS cnt,
                   collect(distinct r.Action) AS actions,
                   count(r.UpdatedAt) AS updated_cnt
            """,
            {"test_id": test_id},
        )
        assert rel_update_check[0]["cnt"] == len(upserted_rel_ids)
        assert rel_update_check[0]["actions"] == ["UPDATE"]
        assert rel_update_check[0]["updated_cnt"] == len(upserted_rel_ids)
    finally:
        # Separate disposal function (can be disabled via "cleanup_enable": false in tests/configs.json)
        cleanup_stats = await cleanup_fhir_test_graph(
            fhir,
            test_id=test_id,
            node_ids=upserted_node_ids,
            relationship_ids=upserted_rel_ids,
            enabled=all_configs["cleanup_enable"],
        )
        if all_configs["cleanup_enable"]:
            assert cleanup_stats["deleted_nodes"] == len(upserted_node_ids)
            remaining = await neo4j_client.execute_query(
                "MATCH (n {test_id: $test_id}) RETURN count(n) AS cnt",
                {"test_id": test_id},
            )
            assert remaining[0]["cnt"] == 0
