"""Integration tests for SearchService against live Neo4j vector index and graph topology."""

from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pytest
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.imagingstudy import ImagingStudy
from fhir.resources.R4B.patient import Patient as FhirPatient

from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model import (
    DEFAULT_EMBEDDING_VECTOR_DIMENSIONS,
    DiagnosticReportFieldTag,
    GroupNode,
    PatientNode,
    SearchIncludeLabel,
    StudyNode,
)
from gosmart_graphite.model.auth import BelongsToRelation
from gosmart_graphite.model.fhir import DiagnosticReportNode
from gosmart_graphite.model.graph import HasStudyRelation
from gosmart_graphite.model.llm_config import LLMConfigCreateRequest
from gosmart_graphite.service.diagnostic_report_service import DiagnosticReportService, ReportTexts
from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG
from gosmart_graphite.service.fhir_service import EncRelevantResources, FhirService
from gosmart_graphite.service.graph_service import GraphService
from gosmart_graphite.service.llm_config_service import LLMConfigService
from gosmart_graphite.service.search_service import SearchService


class _DeterministicStubEmbedder:
    """Stub EmbeddingService returning a controlled L2-normalized 768-dim query vector."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    async def embed_text_async(
        self,
        text: str,
        *,
        is_query: bool = False,
        group_id: Optional[str] = None,
        tag: str = DEFAULT_EMBEDDING_TAG,
        test_id: Optional[str] = None,
        **_kwargs: object,
    ) -> list[float]:
        assert is_query is True
        return list(self._vector)

    def embed_text(
        self,
        text: str,
        *,
        is_query: bool = False,
        group_id: Optional[str] = None,
        tag: str = DEFAULT_EMBEDDING_TAG,
        test_id: Optional[str] = None,
        **_kwargs: object,
    ) -> list[float]:
        assert is_query is True
        return list(self._vector)


def _unit_vec(v0: float, v1: float = 0.0, dims: int = DEFAULT_EMBEDDING_VECTOR_DIMENSIONS) -> list[float]:
    return [v0, v1] + [0.0] * (dims - 2)


async def _upsert_group(grp: GroupNode, test_id: str) -> None:
    await neo4j_client.execute_query(
        """
        MERGE (g:Group {id: $id})
        SET g.name = $name,
            g.code = $code,
            g.group_type = $group_type,
            g.is_active = true,
            g.test_id = $test_id,
            g.Action = 'CREATE',
            g.ActedAt = datetime({timezone: 'UTC'}),
            g.ActedBy = 'pytest',
            g.CreatedAt = datetime({timezone: 'UTC'})
        """,
        {
            "id": grp.id,
            "name": grp.name,
            "code": grp.code,
            "group_type": grp.group_type,
            "test_id": test_id,
        },
    )


async def _link_child_group(child: GroupNode, parent: GroupNode, test_id: str) -> None:
    rel = BelongsToRelation(start_node_id=child.id, end_node_id=parent.id, test_id=test_id, ActedBy="pytest")
    await neo4j_client.execute_query(
        """
        MATCH (c:Group {id: $child_id}), (p:Group {id: $parent_id})
        MERGE (c)-[r:BELONGS_TO {id: $rel_id}]->(p)
        SET r.start_node_id = $child_id,
            r.end_node_id = $parent_id,
            r.test_id = $test_id,
            r.Action = 'CREATE',
            r.ActedAt = datetime({timezone: 'UTC'}),
            r.ActedBy = 'pytest',
            r.CreatedAt = datetime({timezone: 'UTC'})
        """,
        {
            "child_id": child.id,
            "parent_id": parent.id,
            "rel_id": rel.id,
            "test_id": test_id,
        },
    )


@pytest.mark.anyio
async def test_search_service_end_to_end_neo4j_hierarchy_dedup_context_and_soft_delete() -> None:
    """Verify SearchService sync/async search, dedup, group hierarchy, context hydration, soft-delete, and fallback."""
    if not await neo4j_client.verify_connectivity():
        pytest.skip(f"Neo4j database is not reachable at {neo4j_client.settings.DB_CONNECTION_URI}")

    graph_svc = GraphService()
    await graph_svc.init_schema_constraints(force=True)

    test_id = "test_search_service_neo4j"

    # Clean up prior test-generated data BEFORE the test (retain after test for inspection)
    await neo4j_client.execute_query(
        "MATCH (n {test_id: $test_id}) DETACH DELETE n",
        {"test_id": test_id},
    )

    # 1. Seed group hierarchy:
    #    grp_hospital <-[:BELONGS_TO]- grp_clinic_a
    #    grp_hospital <-[:BELONGS_TO]- grp_clinic_b
    #    grp_other (independent)
    grp_hospital = GroupNode(name="Search Hospital", code="SRCH_HOSP", group_type="HEALTH_SYSTEM", test_id=test_id)
    grp_clinic_a = GroupNode(name="Search Clinic A", code="SRCH_CLN_A", group_type="FACILITY", test_id=test_id)
    grp_clinic_b = GroupNode(name="Search Clinic B", code="SRCH_CLN_B", group_type="FACILITY", test_id=test_id)
    grp_other = GroupNode(name="Search Other Org", code="SRCH_OTHER", group_type="FACILITY", test_id=test_id)

    for grp in (grp_hospital, grp_clinic_a, grp_clinic_b, grp_other):
        await _upsert_group(grp, test_id)
    await _link_child_group(grp_clinic_a, grp_hospital, test_id)
    await _link_child_group(grp_clinic_b, grp_hospital, test_id)

    llm_svc = LLMConfigService()
    for grp in (grp_hospital, grp_other):
        await llm_svc.upsert_llm_config(
            LLMConfigCreateRequest(
                tag=DEFAULT_EMBEDDING_TAG,
                provider="ollama",
                model="nomic-embed-text:latest",
                url="http://localhost:11434",
                dimensions=DEFAULT_EMBEDDING_VECTOR_DIMENSIONS,
                assign_group=grp.id,
                test_id=test_id,
            ),
            acted_by="pytest",
        )

    # 2. Seed DICOM Patient + Study for cross-domain linkage (SAME_AS & REFERENCES_STUDY)
    study_uid_a = "1.2.840.10008.9999.search.1"
    dicom_pat = PatientNode(
        patient_id="SRCH-MRN-001",
        patient_name="DOE^SEARCH",
        patient_sex="F",
        test_id=test_id,
        ActedBy="pytest",
    )
    dicom_study = StudyNode(
        study_instance_uid=study_uid_a,
        study_description="CT CHEST SEARCH",
        accession_number="ACC-SRCH-001",
        test_id=test_id,
        ActedBy="pytest",
    )
    has_study_rel = HasStudyRelation(
        start_node_id=dicom_pat.id,
        end_node_id=dicom_study.id,
        test_id=test_id,
        ActedBy="pytest",
    )
    await neo4j_client.execute_query(
        """
        MERGE (p:Patient {id: $p_id})
        SET p.patient_id = $patient_id,
            p.patient_name = $patient_name,
            p.patient_sex = $patient_sex,
            p.test_id = $test_id,
            p.Action = 'CREATE',
            p.ActedAt = datetime({timezone: 'UTC'}),
            p.ActedBy = 'pytest',
            p.CreatedAt = datetime({timezone: 'UTC'})
        MERGE (s:Study {id: $s_id})
        SET s.study_instance_uid = $study_uid,
            s.study_description = $study_desc,
            s.accession_number = $acc,
            s.test_id = $test_id,
            s.Action = 'CREATE',
            s.ActedAt = datetime({timezone: 'UTC'}),
            s.ActedBy = 'pytest',
            s.CreatedAt = datetime({timezone: 'UTC'})
        MERGE (p)-[r:HAS_STUDY {id: $rel_id}]->(s)
        SET r.start_node_id = $p_id,
            r.end_node_id = $s_id,
            r.test_id = $test_id,
            r.Action = 'CREATE',
            r.ActedAt = datetime({timezone: 'UTC'}),
            r.ActedBy = 'pytest',
            r.CreatedAt = datetime({timezone: 'UTC'})
        """,
        {
            "p_id": dicom_pat.id,
            "patient_id": dicom_pat.patient_id,
            "patient_name": dicom_pat.patient_name,
            "patient_sex": dicom_pat.patient_sex,
            "s_id": dicom_study.id,
            "study_uid": dicom_study.study_instance_uid,
            "study_desc": dicom_study.study_description,
            "acc": dicom_study.accession_number,
            "rel_id": has_study_rel.id,
            "test_id": test_id,
        },
    )

    # 3. Seed FHIR resources across Clinic A, Clinic B (sharing the same FHIR_Patient!), and Other Org
    fhir_svc = FhirService()
    shared_patient = FhirPatient.model_validate(
        {
            "resourceType": "Patient",
            "id": "srch-pat-shared",
            "identifier": [{"value": "SRCH-MRN-001"}],
            "name": [{"family": "Doe", "given": ["Search"]}],
            "gender": "female",
        }
    )

    enc_a = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "srch-enc-a",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
            "subject": {"reference": "Patient/srch-pat-shared"},
        }
    )
    img_a = ImagingStudy.model_validate(
        {
            "resourceType": "ImagingStudy",
            "id": "srch-img-a",
            "status": "available",
            "identifier": [{"system": "urn:dicom:uid", "value": f"urn:oid:{study_uid_a}"}],
            "subject": {"reference": "Patient/srch-pat-shared"},
            "encounter": {"reference": "Encounter/srch-enc-a"},
        }
    )
    dr_a = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "srch-dr-a",
            "status": "final",
            "code": {"text": "CT CHEST"},
            "subject": {"reference": "Patient/srch-pat-shared"},
            "encounter": {"reference": "Encounter/srch-enc-a"},
            "imagingStudy": [{"reference": "ImagingStudy/srch-img-a"}],
            "conclusion": "Consistent with pneumonia.",
        }
    )
    dr_deleted = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "srch-dr-deleted",
            "status": "final",
            "code": {"text": "CT CHEST"},
            "subject": {"reference": "Patient/srch-pat-shared"},
            "encounter": {"reference": "Encounter/srch-enc-a"},
            "conclusion": "Deleted clinic A report.",
        }
    )
    up_a = await fhir_svc.upsert_relevant_resources(
        EncRelevantResources(
            encounter=enc_a,
            patient=shared_patient,
            imaging_studies=[img_a],
            diagnostic_reports=[dr_a, dr_deleted],
        ),
        test_id=test_id,
        acted_by="pytest",
    )
    enc_a_node_id = next(n["id"] for n in up_a["nodes"] if n["label"] == "Encounter")
    await fhir_svc.link_encounter_to_group(
        encounter_id=enc_a_node_id, group_id=grp_clinic_a.id, test_id=test_id, acted_by="pytest"
    )

    enc_b = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "srch-enc-b",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
            "subject": {"reference": "Patient/srch-pat-shared"},
        }
    )
    dr_b = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "srch-dr-b",
            "status": "final",
            "code": {"text": "CXR"},
            "subject": {"reference": "Patient/srch-pat-shared"},
            "encounter": {"reference": "Encounter/srch-enc-b"},
            "conclusion": "Mild pleural effusion.",
        }
    )
    up_b = await fhir_svc.upsert_relevant_resources(
        EncRelevantResources(encounter=enc_b, patient=shared_patient, diagnostic_reports=[dr_b]),
        test_id=test_id,
        acted_by="pytest",
    )
    enc_b_node_id = next(n["id"] for n in up_b["nodes"] if n["label"] == "Encounter")
    await fhir_svc.link_encounter_to_group(
        encounter_id=enc_b_node_id, group_id=grp_clinic_b.id, test_id=test_id, acted_by="pytest"
    )

    enc_other = Encounter.model_validate(
        {
            "resourceType": "Encounter",
            "id": "srch-enc-other",
            "status": "finished",
            "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        }
    )
    dr_other = DiagnosticReport.model_validate(
        {
            "resourceType": "DiagnosticReport",
            "id": "srch-dr-other",
            "status": "final",
            "code": {"text": "CT ABDOMEN"},
            "encounter": {"reference": "Encounter/srch-enc-other"},
            "conclusion": "Other organization report.",
        }
    )
    up_other = await fhir_svc.upsert_relevant_resources(
        EncRelevantResources(encounter=enc_other, diagnostic_reports=[dr_other]),
        test_id=test_id,
        acted_by="pytest",
    )
    enc_other_node_id = next(n["id"] for n in up_other["nodes"] if n["label"] == "Encounter")
    await fhir_svc.link_encounter_to_group(
        encounter_id=enc_other_node_id, group_id=grp_other.id, test_id=test_id, acted_by="pytest"
    )

    # 4. Register deterministic vector embeddings for all 4 DiagnosticReports
    dr_svc = DiagnosticReportService(llm_config_service=llm_svc)
    vec_exact = _unit_vec(1.0, 0.0)  # cosine score 1.0 vs query [1, 0, ...]
    vec_high = _unit_vec(0.8, 0.6)  # cosine score 0.9
    vec_mid = _unit_vec(0.6, 0.8)  # cosine score 0.8

    reg_a = dr_svc.graph_register(
        ReportTexts(
            report=dr_a,
            hasText=True,
            findings="Right lower lobe pneumonia.",
            conclusion="Consistent with pneumonia.",
            embedding_findings=vec_exact,
            embedding_conclusion=vec_high,
        ),
        test_id=test_id,
        acted_by="pytest",
    )
    assert reg_a.status == 200

    reg_b = dr_svc.graph_register(
        ReportTexts(
            report=dr_b,
            hasText=True,
            conclusion="Mild pleural effusion.",
            embedding_conclusion=vec_mid,
        ),
        test_id=test_id,
        acted_by="pytest",
    )
    assert reg_b.status == 200

    reg_other = dr_svc.graph_register(
        ReportTexts(
            report=dr_other,
            hasText=True,
            conclusion="Other organization report.",
            embedding_conclusion=vec_high,
        ),
        test_id=test_id,
        acted_by="pytest",
    )
    assert reg_other.status == 200

    reg_del = dr_svc.graph_register(
        ReportTexts(
            report=dr_deleted,
            hasText=True,
            conclusion="Deleted clinic A report.",
            embedding_conclusion=vec_high,
        ),
        test_id=test_id,
        acted_by="pytest",
    )
    assert reg_del.status == 200

    # Soft-delete dr_deleted in Neo4j
    dr_a_id = DiagnosticReportNode(resource_id="srch-dr-a", resource="{}", test_id=test_id).id
    dr_b_id = DiagnosticReportNode(resource_id="srch-dr-b", resource="{}", test_id=test_id).id
    dr_other_id = DiagnosticReportNode(resource_id="srch-dr-other", resource="{}", test_id=test_id).id
    dr_del_id = DiagnosticReportNode(resource_id="srch-dr-deleted", resource="{}", test_id=test_id).id

    await neo4j_client.execute_query(
        "MATCH (dr:DiagnosticReport {id: $id}) SET dr.Action = 'DELETE'",
        {"id": dr_del_id},
    )

    search_svc = SearchService(embedding_service=_DeterministicStubEmbedder(vec_exact))  # type: ignore[arg-type]

    # 5a. Unscoped search (group_id=None) -> deduplicates dr_a (2 embeddings -> 1 result), excludes dr_deleted
    all_hits = await search_svc.search_async("pneumonia", test_id=test_id)
    assert [h.node_id for h in all_hits] == [dr_a_id, dr_other_id, dr_b_id]
    assert all_hits[0].node_label == "DiagnosticReport"
    assert all_hits[0].text == "Right lower lobe pneumonia."
    assert all_hits[0].target_field_tag == DiagnosticReportFieldTag.FINDINGS
    assert all_hits[0].score >= 0.99
    assert all(h.is_deleted is False for h in all_hits)
    assert all(h.encounter is None and h.fhir_patient is None and h.imaging_studies is None for h in all_hits)

    # 5b. Leaf group filter (Clinic A) -> ONLY dr_a (no sibling Clinic B leak via shared patient or Hospital!)
    clinic_a_hits = await search_svc.search_async("pneumonia", group_id=grp_clinic_a.id, test_id=test_id)
    assert [h.node_id for h in clinic_a_hits] == [dr_a_id]

    # 5c. Parent group filter (Hospital) -> returns Clinic A AND Clinic B descendants, excludes Other Org
    hospital_hits = await search_svc.search_async("pneumonia", group_id=grp_hospital.id, test_id=test_id)
    assert [h.node_id for h in hospital_hits] == [dr_a_id, dr_b_id]

    # 5d. Limit & min_score filters
    limited_hits = await search_svc.search_async("pneumonia", group_id=grp_hospital.id, limit=1, test_id=test_id)
    assert [h.node_id for h in limited_hits] == [dr_a_id]

    high_score_hits = await search_svc.search_async(
        "pneumonia", group_id=grp_hospital.id, min_score=0.85, test_id=test_id
    )
    assert [h.node_id for h in high_score_hits] == [dr_a_id]

    # 5e. Soft-delete admin override (include_deleted=True) returns dr_deleted with is_deleted=True
    with_deleted = await search_svc.search_async(
        "pneumonia",
        group_id=grp_clinic_a.id,
        include_deleted=True,
        test_id=test_id,
    )
    assert [h.node_id for h in with_deleted] == [dr_a_id, dr_del_id]
    assert with_deleted[0].is_deleted is False
    assert with_deleted[1].is_deleted is True
    assert with_deleted[1].text == "Deleted clinic A report."

    # 5f. Opt-in context hydration: Encounter, FHIR_Patient, ImagingStudy, DICOM Patient, DICOM Study
    hydrated_hits = await search_svc.search_async(
        "pneumonia",
        group_id=grp_clinic_a.id,
        include=[
            SearchIncludeLabel.ENCOUNTER,
            SearchIncludeLabel.FHIR_PATIENT,
            SearchIncludeLabel.IMAGING_STUDY,
            SearchIncludeLabel.PATIENT,
            SearchIncludeLabel.STUDY,
        ],
        test_id=test_id,
    )
    assert len(hydrated_hits) == 1
    hit = hydrated_hits[0]
    assert hit.encounter is not None and hit.encounter.resource_id == "srch-enc-a"
    assert hit.fhir_patient is not None and hit.fhir_patient.resource_id == "srch-pat-shared"
    assert hit.imaging_studies is not None and [i.resource_id for i in hit.imaging_studies] == ["srch-img-a"]
    assert hit.patient is not None and hit.patient.patient_id == "SRCH-MRN-001"
    assert hit.studies is not None and [s.study_instance_uid for s in hit.studies] == [study_uid_a]

    # 5g. Production isolation (test_id=None) never leaks test-tagged nodes
    prod_hits = await search_svc.search_async("pneumonia", group_id=grp_hospital.id, test_id=None)
    assert all(h.node_id not in {dr_a_id, dr_b_id, dr_other_id, dr_del_id} for h in prod_hits)

    # 5h. Synchronous search() parity with search_async()
    sync_hits = search_svc.search(
        "pneumonia",
        group_id=grp_hospital.id,
        include=[SearchIncludeLabel.ENCOUNTER, SearchIncludeLabel.FHIR_PATIENT],
        test_id=test_id,
    )
    assert [h.node_id for h in sync_hits] == [dr_a_id, dr_b_id]
    assert sync_hits[0].encounter is not None and sync_hits[0].encounter.resource_id == "srch-enc-a"
    assert sync_hits[1].encounter is not None and sync_hits[1].encounter.resource_id == "srch-enc-b"
    assert sync_hits[0].fhir_patient is not None and sync_hits[0].fhir_patient.resource_id == "srch-pat-shared"
    assert sync_hits[0].imaging_studies is None  # not requested

    # 5i. Exact-cosine fallback executes cleanly when HNSW candidate_k saturates
    with patch("gosmart_graphite.service.search_service._compute_candidate_k", return_value=1):
        fallback_hits = await search_svc.search_async(
            "pneumonia",
            group_id=grp_clinic_b.id,
            limit=5,
            test_id=test_id,
        )
    assert [h.node_id for h in fallback_hits] == [dr_b_id]
    assert fallback_hits[0].score == pytest.approx(0.8, abs=1e-4)
