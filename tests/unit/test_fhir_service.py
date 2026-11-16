"""Unit tests for FHIR models, conversion, and mocked HTTP retrieval."""

import httpx
import pytest
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.condition import Condition
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.imagingstudy import ImagingStudy
from fhir.resources.R4B.location import Location
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.procedure import Procedure
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.servicerequest import ServiceRequest

from gosmart_graphite.model.fhir import LocationNode, OrganizationNode, PractitionerNode
from gosmart_graphite.service.fhir_service import (
    EncounterContext,
    EncRelevantResources,
    FHIRBatchFetcher,
    FhirService,
    location_to_node,
    organization_to_node,
    practitioner_to_node,
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


def test_convert_full_location_organization_practitioner_nodes():
    """Verify full Location, Organization, and Practitioner resource conversion to graph nodes."""
    loc = Location.model_validate(
        {
            "resourceType": "Location",
            "id": "loc-full-1",
            "status": "active",
            "name": "Radiology Wing B",
            "alias": ["Rad-B"],
            "mode": "instance",
            "telecom": [{"system": "phone", "value": "555-0100", "use": "work"}],
            "address": {"line": ["100 Main St"], "city": "Boston", "state": "MA", "postalCode": "02115"},
            "managingOrganization": {"reference": "Organization/org-full-1", "display": "Boston General"},
        }
    )
    org = Organization.model_validate(
        {
            "resourceType": "Organization",
            "id": "org-full-1",
            "active": True,
            "name": "Boston General",
            "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1234567890"}],
            "telecom": [{"system": "phone", "value": "555-0001"}],
            "address": [{"text": "100 Main St, Boston, MA"}],
        }
    )
    pract = Practitioner.model_validate(
        {
            "resourceType": "Practitioner",
            "id": "pract-full-1",
            "active": True,
            "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "9988776655"}],
            "name": [{"prefix": ["Dr."], "given": ["Sarah"], "family": "Lin", "suffix": ["MD"]}],
            "gender": "female",
            "qualification": [
                {
                    "code": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0360", "code": "MD"}]},
                    "issuer": {"reference": "Organization/org-full-1", "display": "Boston General"},
                }
            ],
        }
    )

    loc_node = location_to_node(loc, test_id="t1")
    assert loc_node.name == "Radiology Wing B"
    assert loc_node.address == "100 Main St, Boston, MA, 02115"
    assert loc_node.telecom == ["phone|555-0100|work"]
    assert loc_node.managingOrganization == "Organization/org-full-1|Boston General"

    org_node = organization_to_node(org, test_id="t1")
    assert org_node.name == "Boston General"
    assert org_node.status == "active"
    assert org_node.address == ["100 Main St, Boston, MA"]
    assert "|1234567890" in (org_node.identifiers or [])

    pract_node = practitioner_to_node(pract, test_id="t1")
    assert pract_node.name == ["Dr. Sarah Lin MD"]
    assert pract_node.gender == "female"
    assert pract_node.qualificationCode == ["http://terminology.hl7.org/CodeSystem/v2-0360|MD"]
    assert pract_node.qualificationIssuer == ["Organization/org-full-1|Boston General"]


def test_convert_auto_generates_stub_nodes_from_references():
    """Verify LocationNode, OrganizationNode, and PractitionerNode are synthesized from references with display."""
    pat = Patient.model_validate(
        {
            "resourceType": "Patient",
            "id": "pat-ref-1",
            "managingOrganization": {"reference": "Organization/org-pat-1", "display": "Care Network"},
        }
    )
    enc = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-ref-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
            "subject": {"reference": "Patient/pat-ref-1"},
            "location": [{"location": {"reference": "Location/loc-enc-1", "display": "Outpatient Clinic"}}],
            "serviceProvider": {"reference": "Organization/org-sp-1", "display": "Metro Hospital"},
            "participant": [{"individual": {"reference": "Practitioner/pract-1", "display": "Dr. Alice Wong"}}],
        }
    )
    sr = ServiceRequest.model_validate(
        {
            "resourceType": "ServiceRequest",
            "id": "sr-ref-1",
            "status": "active",
            "intent": "order",
            "code": {"coding": [{"system": "http://loinc.org", "code": "24627-2"}]},
            "subject": {"reference": "Patient/pat-ref-1"},
            "encounter": {"reference": "Encounter/enc-ref-1"},
            "requester": {"reference": "Practitioner/pract-1", "display": "Dr. Alice Wong"},
        }
    )
    img = ImagingStudy.model_validate(
        {
            "resourceType": "ImagingStudy",
            "id": "is-ref-1",
            "status": "available",
            "subject": {"reference": "Patient/pat-ref-1"},
            "encounter": {"reference": "Encounter/enc-ref-1"},
            "location": {"reference": "Location/loc-nodisp"},
        }
    )
    dr = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "dr-ref-1",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "24627-2"}]},
            "subject": {"reference": "Patient/pat-ref-1"},
            "encounter": {"reference": "Encounter/enc-ref-1"},
            "performer": [
                {"reference": "Practitioner/pract-rad-1", "display": "Dr. Bob Radiologist"},
                {"reference": "Organization/org-sp-1"},
            ],
        }
    )
    obs = Observation.model_validate(
        {
            "resourceType": "Observation",
            "id": "obs-ref-1",
            "status": "final",
            "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
            "subject": {"reference": "Patient/pat-ref-1"},
            "encounter": {"reference": "Encounter/enc-ref-1"},
            "performer": [{"reference": "Practitioner/pract-rad-1"}],
        }
    )
    proc = Procedure.model_validate(
        {
            "resourceType": "Procedure",
            "id": "proc-ref-1",
            "status": "completed",
            "code": {"coding": [{"system": "http://snomed.info/sct", "code": "399208008"}]},
            "subject": {"reference": "Patient/pat-ref-1"},
            "encounter": {"reference": "Encounter/enc-ref-1"},
            "location": {"reference": "Location/loc-nodisp", "display": "CT Suite 2"},
            "performer": [{"actor": {"reference": "Practitioner/pract-1"}}],
            "recorder": {"reference": "Practitioner/pract-rec-1", "display": "Nurse Jane"},
        }
    )

    bundle = EncRelevantResources(
        patient=pat,
        encounter=enc,
        serviceRequests=[sr],
        imaging_studies=[img],
        diagnostic_reports=[dr],
        observations=[obs],
        procedures=[proc],
    )

    svc = FhirService()
    nodes, relations = svc.convert_relevant_resources(bundle, test_id="unit_stub_gen", acted_by="tester")

    locs = {n.resource_id: n for n in nodes if isinstance(n, LocationNode)}
    orgs = {n.resource_id: n for n in nodes if isinstance(n, OrganizationNode)}
    practs = {n.resource_id: n for n in nodes if isinstance(n, PractitionerNode)}

    assert set(locs.keys()) == {"loc-enc-1", "loc-nodisp"}
    assert locs["loc-enc-1"].name == "Outpatient Clinic"
    # Backfilled from Procedure.location display even though ImagingStudy.location lacked display
    assert locs["loc-nodisp"].name == "CT Suite 2"

    assert set(orgs.keys()) == {"org-pat-1", "org-sp-1"}
    assert orgs["org-pat-1"].name == "Care Network"
    assert orgs["org-sp-1"].name == "Metro Hospital"

    assert set(practs.keys()) == {"pract-1", "pract-rad-1", "pract-rec-1"}
    assert practs["pract-1"].name == ["Dr. Alice Wong"]
    assert practs["pract-rad-1"].name == ["Dr. Bob Radiologist"]
    assert practs["pract-rec-1"].name == ["Nurse Jane"]

    rel_pairs = {(r.__type__, r.start_node_id, r.end_node_id) for r in relations}
    enc_node = next(n for n in nodes if n.__label__ == "Encounter")
    pat_node = next(n for n in nodes if n.__label__ == "FHIR_Patient")
    sr_node = next(n for n in nodes if n.__label__ == "ServiceRequest")
    is_node = next(n for n in nodes if n.__label__ == "ImagingStudy")
    dr_node = next(n for n in nodes if n.__label__ == "DiagnosticReport")
    obs_node = next(n for n in nodes if n.__label__ == "Observation")
    proc_node = next(n for n in nodes if n.__label__ == "Procedure")

    assert ("PERFORMED_AT", enc_node.id, locs["loc-enc-1"].id) in rel_pairs
    assert ("PERFORMED_AT", is_node.id, locs["loc-nodisp"].id) in rel_pairs
    assert ("PERFORMED_AT", proc_node.id, locs["loc-nodisp"].id) in rel_pairs
    assert ("SERVICE_PROVIDER", enc_node.id, orgs["org-sp-1"].id) in rel_pairs
    assert ("PART_OF", pat_node.id, orgs["org-pat-1"].id) in rel_pairs
    assert ("REQUESTS", practs["pract-1"].id, sr_node.id) in rel_pairs
    assert ("CREATES", practs["pract-rad-1"].id, dr_node.id) in rel_pairs
    assert ("CREATES", orgs["org-sp-1"].id, dr_node.id) in rel_pairs
    assert ("PERFORMS", practs["pract-1"].id, enc_node.id) in rel_pairs
    assert ("PERFORMS", practs["pract-rad-1"].id, obs_node.id) in rel_pairs
    assert ("PERFORMS", practs["pract-1"].id, proc_node.id) in rel_pairs


@pytest.mark.anyio
async def test_fetch_encounter_graph_fetches_loc_org_pract_with_stub_fallback():
    """Verify fetch_encounter_graph fetches referenced Location/Organization/Practitioner and allows 404 fallback."""
    enc = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-fetch-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
            "subject": {"reference": "Patient/pat-fetch-1"},
            "location": [{"location": {"reference": "Location/loc-live-1", "display": "Main Radiology"}}],
            "serviceProvider": {"reference": "Organization/org-live-1", "display": "General Hospital"},
            "participant": [{"individual": {"reference": "Practitioner/pract-live-1", "display": "Dr. Live"}}],
        }
    )
    raw_img = {
        "resourceType": "ImagingStudy",
        "id": "is-fetch-1",
        "status": "available",
        "subject": {"reference": "Patient/pat-fetch-1"},
        "encounter": {"reference": "Encounter/enc-fetch-1"},
        "location": {"reference": "Location/loc-missing-404", "display": "Fallback Suite"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/Patient/pat-fetch-1"):
            return httpx.Response(200, json={"resourceType": "Patient", "id": "pat-fetch-1"})
        if path.endswith("/ImagingStudy"):
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"resource": raw_img}]})
        if path.endswith("/Location/loc-live-1"):
            return httpx.Response(200, json={"resourceType": "Location", "id": "loc-live-1", "name": "Main Radiology"})
        if path.endswith("/Location/loc-missing-404"):
            return httpx.Response(404)
        if path.endswith("/Organization/org-live-1"):
            return httpx.Response(
                200, json={"resourceType": "Organization", "id": "org-live-1", "name": "General Hospital"}
            )
        if path.endswith("/Practitioner/pract-live-1"):
            return httpx.Response(
                200,
                json={"resourceType": "Practitioner", "id": "pract-live-1", "name": [{"text": "Dr. Live"}]},
            )
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = FHIRBatchFetcher(base_url="http://mock-fhir/fhir/r4", client=client)
        svc = FhirService(fetcher=fetcher)
        relevant = await svc.fetch_enc_relevant_resources(enc)

        assert len(relevant.locations) == 1
        assert relevant.locations[0].id == "loc-live-1"
        assert len(relevant.organizations) == 1
        assert relevant.organizations[0].id == "org-live-1"
        assert len(relevant.practitioners) == 1
        assert relevant.practitioners[0].id == "pract-live-1"
        assert relevant.errors == []

        nodes, _ = svc.convert_relevant_resources(relevant, test_id="fetch_fallback_test")
        locs = {n.resource_id: n for n in nodes if isinstance(n, LocationNode)}
        assert set(locs.keys()) == {"loc-live-1", "loc-missing-404"}
        assert locs["loc-missing-404"].name == "Fallback Suite"


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
