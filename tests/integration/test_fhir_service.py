"""Integration tests for FHIR graph upsert and cleanup against Neo4j."""

from typing import Optional

import pytest
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.imagingstudy import ImagingStudy
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.servicerequest import ServiceRequest

from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model.dicom import DicomHierarchy, InstanceModel, PatientModel, SeriesModel, StudyModel
from gosmart_graphite.service.fhir_service import EncRelevantResources, FhirService
from gosmart_graphite.service.graph_service import graph_service
from tests.utils.config_utils import ConfigUtils

all_configs = ConfigUtils().configs


async def cleanup_fhir_test_graph(
    svc: FhirService,
    test_id: Optional[str] = None,
    node_ids: Optional[list[str]] = None,
    relationship_ids: Optional[list[str]] = None,
    enabled: bool = all_configs["cleanup_enable"],
) -> dict[str, int]:
    """Clean up only resources created by the current test, when enabled."""
    if not enabled:
        return {"deleted_nodes": 0, "deleted_relationships": 0}
    return await svc.cleanup_upserted_resources(
        test_id=test_id,
        node_ids=node_ids,
        relationship_ids=relationship_ids,
    )


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


@pytest.mark.anyio
async def test_upsert_stub_non_clobbering_and_upgrade():
    """Verify reference-stub upserts preserve existing full resources and upgrade cleanly when full arrives."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    test_id = "test_fhir_stub_upgrade"
    svc = FhirService()

    # Clean up before test starts (keep results inspectable if cleanup_enable=false)
    await svc.cleanup_upserted_resources(test_id=test_id)

    enc_pass1 = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "enc-stub-1",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
            "location": [{"location": {"reference": "Location/loc-upg-1", "display": "Full Location"}}],
            "serviceProvider": {"reference": "Organization/org-upg-1", "display": "Full Org"},
            "participant": [{"individual": {"reference": "Practitioner/pract-upg-1", "display": "Dr. Stub"}}],
        }
    )
    full_org = Organization.model_validate(
        {
            "resourceType": "Organization",
            "id": "org-upg-1",
            "active": True,
            "name": "Full Organization Name",
            "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1112223334"}],
        }
    )
    full_pract = Practitioner.model_validate(
        {
            "resourceType": "Practitioner",
            "id": "pract-upg-1",
            "active": True,
            "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "5556667778"}],
            "name": [{"text": "Dr. Full Practitioner, MD"}],
        }
    )

    # Pass 1: Full Organization + Stub Location + Stub Practitioner
    res1 = await svc.upsert_relevant_resources(
        EncRelevantResources(encounter=enc_pass1, organizations=[full_org]),
        test_id=test_id,
        acted_by="pytest_pass1",
    )

    # Pass 2: Stub Organization (no organizations list) + Stub Location + Full Practitioner
    res2 = await svc.upsert_relevant_resources(
        EncRelevantResources(encounter=enc_pass1, practitioners=[full_pract]),
        test_id=test_id,
        acted_by="pytest_pass2",
    )

    try:
        org_rows = await neo4j_client.execute_query(
            "MATCH (o:Organization {resource_id: 'org-upg-1', test_id: $test_id}) "
            "RETURN o.name AS name, o.identifiers AS identifiers, o.resource AS resource",
            {"test_id": test_id},
        )
        assert len(org_rows) == 1
        # Stub in Pass 2 must NOT overwrite full Organization name or identifiers
        assert org_rows[0]["name"] == "Full Organization Name"
        assert "|1112223334" in (org_rows[0]["identifiers"] or [])
        assert '"identifier"' in (org_rows[0]["resource"] or "")

        pract_rows = await neo4j_client.execute_query(
            "MATCH (p:Practitioner {resource_id: 'pract-upg-1', test_id: $test_id}) "
            "RETURN p.name AS name, p.identifiers AS identifiers",
            {"test_id": test_id},
        )
        assert len(pract_rows) == 1
        # Full Practitioner in Pass 2 upgrades the Pass 1 stub
        assert pract_rows[0]["name"] == ["Dr. Full Practitioner, MD"]
        assert "|5556667778" in (pract_rows[0]["identifiers"] or [])

        loc_rows = await neo4j_client.execute_query(
            "MATCH (l:Location {resource_id: 'loc-upg-1', test_id: $test_id}) RETURN l.name AS name",
            {"test_id": test_id},
        )
        assert len(loc_rows) == 1
        assert loc_rows[0]["name"] == "Full Location"

        rel_rows = await neo4j_client.execute_query(
            "MATCH (e:Encounter {resource_id: 'enc-stub-1', test_id: $test_id})-[r]->(t) "
            "RETURN type(r) AS rel_type, labels(t) AS target_labels",
            {"test_id": test_id},
        )
        rel_summary = {(r["rel_type"], tuple(r["target_labels"])) for r in rel_rows}
        assert ("PERFORMED_AT", ("Location",)) in rel_summary
        assert ("SERVICE_PROVIDER", ("Organization",)) in rel_summary
    finally:
        await cleanup_fhir_test_graph(
            svc,
            test_id=test_id,
            node_ids=res2["node_ids"],
            relationship_ids=res2["relationship_ids"],
            enabled=all_configs["cleanup_enable"],
        )
        if res1 and not res2:
            await cleanup_fhir_test_graph(
                svc,
                test_id=test_id,
                node_ids=res1["node_ids"],
                relationship_ids=res1["relationship_ids"],
                enabled=all_configs["cleanup_enable"],
            )
