from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from gosmart_graphite.model.fhir import (
    FHIR_ALL_SCHEMA_DDL,
    FHIR_NODE_CLASSES,
    FHIR_NODE_LABELS,
    FHIR_RELATION_CLASSES,
    FHIR_SCHEMA_CONSTRAINTS,
    FHIR_SCHEMA_INDEXES,
    MAX_INLINE_RESOURCE_BYTES,
    CodedValueMacro,
    ConditionNode,
    DiagnosticReportNode,
    EncounterNode,
    FHIRPatientNode,
    FulfilledByRelation,
    ImagingStudyNode,
    LocationNode,
    ObservationNode,
    OrganizationNode,
    PractitionerNode,
    ProcedureNode,
    SameAsRelation,
    ServiceRequestNode,
    SubjectRelation,
    flatten_codings,
    flatten_identifiers,
    format_coding_macro,
    format_identifier_macro,
    format_reference_macro,
    generate_fhir_cypher_script,
    get_node_indexed_properties,
)


def test_identifier_macro_formatting() -> None:
    entries = format_identifier_macro(
        "https://github.com/synthetichealth/synthea",
        "b86d32b0-5bd7-2bbd-c575-b07bfaa9270e",
    )
    assert entries == [
        "https://github.com/synthetichealth/synthea|b86d32b0-5bd7-2bbd-c575-b07bfaa9270e",
        "|b86d32b0-5bd7-2bbd-c575-b07bfaa9270e",
        "https://github.com/synthetichealth/synthea|",
    ]

    flattened = flatten_identifiers(
        [
            {
                "use": "official",
                "system": "https://github.com/synthetichealth/synthea",
                "value": "b86d32b0-5bd7-2bbd-c575-b07bfaa9270e",
            },
            {
                "system": "http://radiology.example.org/accession",
                "value": "ACC-9948271",
            },
        ]
    )
    assert "|ACC-9948271" in flattened
    assert "http://radiology.example.org/accession|ACC-9948271" in flattened


def test_coding_and_reference_macros() -> None:
    assert (
        format_coding_macro("http://loinc.org", "24606-6", "MG Breast Screening")
        == "http://loinc.org|24606-6|MG Breast Screening"
    )
    assert format_coding_macro("http://loinc.org", "24606-6") == "http://loinc.org|24606-6"

    codings = flatten_codings(
        {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": "24627-2",
                    "display": "CT Chest W contrast IV",
                },
                {
                    "system": "http://www.ama-assn.org/go/cpt",
                    "code": "71260",
                },
            ],
            "text": "CT Chest with IV Contrast",
        }
    )
    assert codings == [
        "http://loinc.org|24627-2|CT Chest W contrast IV",
        "http://www.ama-assn.org/go/cpt|71260",
    ]

    assert format_reference_macro("Location/1237", "A Medical Center") == "Location/1237|A Medical Center"
    assert format_reference_macro("Patient/pat-10492") == "Patient/pat-10492"


def test_coded_value_macro_validation() -> None:
    cv = CodedValueMacro(
        codes=["http://loinc.org|20565-8|Carbon dioxide, total [Moles/volume] in Blood"],
        numericValues=[25.1],
        units=["http://unitsofmeasure.org|mmol/L|mmol/L"],
    )
    assert cv.numericValues == [25.1]

    with pytest.raises(ValidationError, match="identical lengths"):
        CodedValueMacro(
            codes=["http://loinc.org|20565-8|CO2"],
            numericValues=[25.1, 30.0],
            units=["http://unitsofmeasure.org|mmol/L|mmol/L"],
        )


def test_fhir_domain_resource_payload_invariants() -> None:
    # 1. Both resource and resourceUri null -> rejected
    with pytest.raises(ValidationError, match="At least one of 'resource' or 'resourceUri' must be non-null"):
        FHIRPatientNode(
            uid="synthea:pat-1",
            resource_id="pat-1",
        )

    # 2. Inline resource <= 4 KB -> accepted & deterministic UUIDv5 populated
    small_json = '{"resourceType":"Patient","id":"pat-1"}'
    p1 = FHIRPatientNode(
        uid="synthea:pat-1",
        resource_id="pat-1",
        status="active",
        resource=small_json,
        identifiers=format_identifier_macro("http://hospital.example.org/mrn", "MRN-1001"),
    )
    assert p1.id
    assert "|MRN-1001" in p1.identifiers

    # 3. Default uid derived from resource_id when omitted
    p_default_uid = FHIRPatientNode(
        resource_id="pat-2",
        resource=small_json,
    )
    assert p_default_uid.uid == "fhir:pat-2"

    # 4. Inline resource > 4 KB (4096 bytes) -> accepted inline
    oversized_json = '{"data":"' + ("x" * MAX_INLINE_RESOURCE_BYTES) + '"}'
    p_oversized = FHIRPatientNode(
        uid="synthea:pat-oversized",
        resource_id="pat-oversized",
        resource=oversized_json,
    )
    assert p_oversized.resource == oversized_json

    # 5. External URI -> accepted, and both populated simultaneously (archived) -> accepted
    p_archived = FHIRPatientNode(
        uid="synthea:pat-archived",
        resource_id="pat-archived",
        resource=small_json,
        resourceUri="gs://fhir-archive-bucket/Patient/pat-archived.json",
    )
    assert p_archived.resource == small_json
    assert p_archived.resourceUri == "gs://fhir-archive-bucket/Patient/pat-archived.json"


def test_all_fhir_resource_nodes_instantiation() -> None:
    now = datetime.now(timezone.utc)
    raw = '{"resourceType":"Stub"}'

    enc = EncounterNode(
        uid="src:enc-1",
        resource_id="enc-1",
        status="finished",
        resource=raw,
        identifiers=["|ENC-1"],
        **{"class": "http://terminology.hl7.org/CodeSystem/v3-ActCode|IMP"},
        type=["http://snomed.info/sct|308335008|Patient encounter procedure"],
        subject="Patient/pat-1|Jane Doe",
        periodStart=now,
    )
    dumped_enc = enc.model_dump(by_alias=True)
    assert dumped_enc["class"] == "http://terminology.hl7.org/CodeSystem/v3-ActCode|IMP"

    cond = ConditionNode(
        uid="src:cond-1",
        resource_id="cond-1",
        status="active",
        resource=raw,
        clinicalStatus=["http://terminology.hl7.org/CodeSystem/condition-clinical|active"],
        verificationStatus=["http://terminology.hl7.org/CodeSystem/condition-ver-status|confirmed"],
        code=["http://hl7.org/fhir/sid/icd-10-cm|R91.1|Solitary pulmonary nodule"],
        subject="Patient/pat-1",
    )
    assert cond.__label__ == "Condition"

    img = ImagingStudyNode(
        uid="src:is-1",
        resource_id="is-1",
        status="available",
        resourceUri="s3://fhir-bucket/ImagingStudy/is-1.json",
        identifiers=["|ACC-9948271", "urn:dicom:uid|urn:oid:1.2.840.113619.2.55.3"],
        subject="Patient/pat-1",
        basedOn=["ServiceRequest/sr-ct-chest-contrast-001"],
        studyUid="1.2.840.113619.2.55.3",
        seriesUid=["1.2.840.113619.2.55.3.1"],
        instanceUid=["1.2.840.113619.2.55.3.1.1"],
        seriesModality=["http://dicom.nema.org/resources/ontology/DCM|CT"],
    )
    assert img.studyUid == "1.2.840.113619.2.55.3"

    loc = LocationNode(
        uid="src:loc-1",
        resource_id="loc-1",
        status="active",
        resource=raw,
        name="CT Suite 1",
        mode="instance",
        managingOrganization="Organization/org-1|General Hospital",
    )
    assert loc.__label__ == "Location"

    obs = ObservationNode(
        uid="src:obs-1",
        resource_id="obs-1",
        status="final",
        resource=raw,
        code=["http://loinc.org|20565-8|Carbon dioxide"],
        subject="Patient/pat-1",
        codes=["http://loinc.org|20565-8|Carbon dioxide"],
        numericValues=[25.1],
        units=["http://unitsofmeasure.org|mmol/L|mmol/L"],
    )
    assert obs.numericValues == [25.1]

    with pytest.raises(ValidationError, match="identical array lengths"):
        ObservationNode(
            uid="src:obs-bad",
            resource_id="obs-bad",
            resource=raw,
            codes=["http://loinc.org|20565-8|Carbon dioxide"],
            numericValues=[25.1, 99.9],
            units=["http://unitsofmeasure.org|mmol/L|mmol/L"],
        )

    org = OrganizationNode(
        uid="src:org-1",
        resource_id="org-1",
        active=True,
        resource=raw,
        name="General Hospital",
        identifiers=["http://hl7.org/fhir/sid/us-npi|1234567890", "|1234567890"],
    )
    assert org.active is True

    dr = DiagnosticReportNode(
        uid="src:dr-1",
        resource_id="dx-report-radiology",
        status="final",
        resource=raw,
        basedOn=["ServiceRequest/sr-ct-chest-contrast-001"],
        imagingStudy=["ImagingStudy/is-1"],
        code=["http://loinc.org|24606-6|MG Breast Screening"],
        subject="Patient/pat-1",
        conclusion="BI-RADS Category 1 - Negative.",
    )
    assert dr.status == "final"

    pract = PractitionerNode(
        uid="src:pract-1",
        resource_id="dr-pulmonologist-12",
        active=True,
        resource=raw,
        name=["Dr. Sarah Lin, MD"],
        specialty=["http://snomed.info/sct|418112009|Pulmonary medicine"],
        organization=["Organization/org-1|General Hospital"],
    )
    assert pract.__label__ == "Practitioner"

    sr = ServiceRequestNode(
        uid="src:sr-1",
        resource_id="sr-ct-chest-contrast-001",
        status="active",
        intent="order",
        resource=raw,
        identifiers=["http://radiology.example.org/accession|ACC-9948271", "|ACC-9948271"],
        identifierType=["http://terminology.hl7.org/CodeSystem/v2-0203|ACSN|Accession ID"],
        code=["http://loinc.org|24627-2|CT Chest W contrast IV"],
        subject="Patient/pat-1|Jane Doe",
        requester="Practitioner/dr-pulmonologist-12|Dr. Sarah Lin, MD",
    )
    assert "|ACC-9948271" in sr.identifiers

    proc = ProcedureNode(
        uid="src:proc-1",
        resource_id="proc-1",
        status="completed",
        resource=raw,
        code=["http://snomed.info/sct|77477000|CT of chest"],
        subject="Patient/pat-1",
    )
    assert proc.__label__ == "Procedure"


def test_fhir_relations_and_negation() -> None:
    assert len(FHIR_RELATION_CLASSES) == 18
    rel = SameAsRelation(start_node_id="dicom-pat-uuid", end_node_id="fhir-pat-uuid")
    assert rel.__type__ == "SAME_AS"
    assert rel.to_negated_type() == "NOT_SAME_AS"
    assert rel.id

    sub_rel = SubjectRelation(start_node_id="sr-uuid", end_node_id="fhir-pat-uuid")
    assert sub_rel.__type__ == "SUBJECT"

    fulfilled_rel = FulfilledByRelation(start_node_id="sr-uuid", end_node_id="dicom-study-uuid")
    assert fulfilled_rel.__type__ == "FULFILLED_BY"


def test_cypher_schema_constraints_and_indexes_synchronization() -> None:
    assert len(FHIR_NODE_CLASSES) == 11
    assert len(FHIR_NODE_LABELS) == 11
    # 2 constraints (id and uid) per FHIR node label
    assert len(FHIR_SCHEMA_CONSTRAINTS) == 22
    assert len(FHIR_ALL_SCHEMA_DDL) == len(FHIR_SCHEMA_CONSTRAINTS) + len(FHIR_SCHEMA_INDEXES)

    # Verify every indexed property of every FHIR node class is represented in FHIR_SCHEMA_INDEXES
    joined_indexes = "\n".join(FHIR_SCHEMA_INDEXES)
    assert "FOR (s:Study) ON (s.accession_number)" in joined_indexes

    for node_cls in FHIR_NODE_CLASSES:
        label = node_cls.__label__
        props = get_node_indexed_properties(node_cls)
        assert "resource" not in props
        for prop in props:
            expected_prop = f"n.`{prop}`" if prop == "class" else f"n.{prop}"
            assert f"FOR (n:{label}) ON ({expected_prop})" in joined_indexes

    # Verify src/db/fhir_schema.cypher matches generate_fhir_cypher_script()
    cypher_file = Path(__file__).resolve().parent.parent / "src" / "gosmart_graphite" / "db" / "fhir_schema.cypher"
    assert cypher_file.exists()
    assert cypher_file.read_text(encoding="utf-8") == generate_fhir_cypher_script()
