# Diagnostic Report Structure Design Node

## Introduction

This design node outlines the structure of the diagnostic report, which is a
crucial component of the diagnostic system. The diagnostic report provides
detailed information about the diagnostic process, including the diagnostic
results, the diagnostic context, and the diagnostic actions taken.

## The Goal

- Allow efficient vector embedding search based on the report text provided in
  DiagnosticReport resource

## DiagnosticReport Data - `presentedForm` Property

The `presentedForm` property contains either a text string or sometimes a PDF
file encapsulated. For this version we only will handle the text based report
as PDF text extraction will require addition coding effort.

### DiagnosticReport Data - text extraction.

## Appendix

### Finding `DiagnosticReport` nodes with `presentedForm` property

This query allows us to find them:

```cypher
MATCH (d:DiagnosticReport)
WHERE d.presentedFormData IS NOT NULL AND size(d.presentedFormData) > 0
RETURN d
```

### Example Algorithm to Find the Actual Data Contained in the `presentedForm`

The presentedForm is a list of `Attachment` type resource which contain both
the content and contentType values. But this is not reliable as the contentType
is often mislabeled.

We can use the Magic Bytes approach to figure out the actual data contained
in the `presentedForm` property instead of "often mislabeled".

```python
import base64
import re
from typing import Optional


def detect_attachment_type(base64_data: str) -> str:
    """
    Detects the true MIME type of base64-encoded attachment data
    by inspecting leading magic bytes.
    """
    if not base64_data:
        return "application/octet-stream"

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
    except Exception:
        return "application/octet-stream"

    # Strip UTF-8 BOM if present (\xef\xbb\xbf)
    if header.startswith(b"\xef\xbb\xbf"):
        header = header[3:]
    header_lstrip = header.lstrip()

    # 1. PDF Check
    # PDF specification ISO 32000-1 allows up to 1024 bytes of header before %PDF-
    if b"%PDF-" in header[:32]:
        return "application/pdf"

    # 2. RTF Check
    if header_lstrip.startswith(b"{\\rtf"):
        return "application/rtf"

    # 3. HTML / XML Check
    header_lower = header_lstrip.lower()
    if (
            header_lower.startswith(b"<!doctype html")
            or header_lower.startswith(b"<html")
    ):
        return "text/html"
    if header_lower.startswith(b"<?xml"):
        return "application/xml"

    # 4. Text Check (verify printable ASCII / UTF-8)
    try:
        decoded_sample = header.decode("utf-8")
        # Check for unprintable binary control characters
        if not any(
                ord(c) < 32 and c not in "\r\n\t\f\b" for c in decoded_sample):
            return "text/plain"
    except UnicodeDecodeError:
        pass

    return "application/octet-stream"
```

## Sample DiagnosticReport

```json
{
  "resourceType": "DiagnosticReport",
  "id": "183763",
  "meta": {
    "versionId": "1",
    "lastUpdated": "2026-10-21T13:08:16.314+00:00",
    "profile": [
      "http://hl7.org/fhir/us/core/StructureDefinition/us-core-diagnosticreport-note"
    ]
  },
  "status": "final",
  "category": [
    {
      "coding": [
        {
          "system": "http://loinc.org",
          "code": "34117-2",
          "display": "History and physical note"
        },
        {
          "system": "http://loinc.org",
          "code": "51847-2",
          "display": "Evaluation + Plan note"
        }
      ]
    }
  ],
  "code": {
    "coding": [
      {
        "system": "http://loinc.org",
        "code": "34117-2",
        "display": "History and physical note"
      },
      {
        "system": "http://loinc.org",
        "code": "51847-2",
        "display": "Evaluation + Plan note"
      }
    ]
  },
  "subject": {
    "reference": "Patient/182539"
  },
  "encounter": {
    "reference": "Encounter/183748"
  },
  "effectiveDateTime": "2016-01-10T21:45:51-08:00",
  "issued": "2016-01-10T21:45:51.212-08:00",
  "performer": [
    {
      "reference": "Practitioner/1989",
      "display": "Dr. Ted955 Reilly981"
    }
  ],
  "presentedForm": [
    {
      "contentType": "text/plain; charset=utf-8",
      "data": "CjIwMTYtMDEtMTAKCiMgQ2hpZWYgQ29tcGxhaW50Ck5vIGNvbXBsYWludHMuCgojIEhpc3Rvcnkgb2YgUHJlc2VudCBJbGxuZXNzCkZsb3JyaWU0NTMgQWphODQ4IGlzIGEgODAgeWVhci1vbGQgbm9uaGlzcGFuaWMgd2hpdGUgZmVtYWxlLiBQYXRpZW50IGhhcyBhIGhpc3Rvcnkgb2YgZ2luZ2l2YWwgZGlzZWFzZSAoZGlzb3JkZXIpLCBtZWRpY2F0aW9uIHJldmlldyBkdWUgKHNpdHVhdGlvbiksIHVuZW1wbG95ZWQgKGZpbmRpbmcpLCBhY3V0ZSB2aXJhbCBwaGFyeW5naXRpcyAoZGlzb3JkZXIpLCB2aWN0aW0gb2YgaW50aW1hdGUgcGFydG5lciBhYnVzZSAoZmluZGluZyksIHZpcmFsIHNpbnVzaXRpcyAoZGlzb3JkZXIpLCBsaW1pdGVkIHNvY2lhbCBjb250YWN0IChmaW5kaW5nKSwgbm90IGluIGxhYm9yIGZvcmNlIChmaW5kaW5nKSwgZnVsbC10aW1lIGVtcGxveW1lbnQgKGZpbmRpbmcpLCBndW5zaG90IHdvdW5kIChkaXNvcmRlciksIGJ1bGxldCB3b3VuZCAoZGlzb3JkZXIpLCBnaW5naXZpdGlzIChkaXNvcmRlciksIHN0cmVzcyAoZmluZGluZyksIHNvY2lhbCBpc29sYXRpb24gKGZpbmRpbmcpLgoKIyBTb2NpYWwgSGlzdG9yeQpQYXRpZW50IGlzIHNpbmdsZS4gUGF0aWVudCBoYXMgbmV2ZXIgc21va2VkLgogUGF0aWVudCBpZGVudGlmaWVzIGFzIGhldGVyb3NleHVhbC4KClBhdGllbnQgY29tZXMgZnJvbSBhIG1pZGRsZSBzb2Npb2Vjb25vbWljIGJhY2tncm91bmQuCiBQYXRpZW50IGRpZCBub3QgZmluaXNoIGhpZ2ggc2Nob29sLgpQYXRpZW50IGN1cnJlbnRseSBoYXMgTWVkaWNhcmUuCgojIEFsbGVyZ2llcwpObyBLbm93biBBbGxlcmdpZXMuCgojIE1lZGljYXRpb25zCmxpc2lub3ByaWwgMTAgbWcgb3JhbCB0YWJsZXQ7IDEwIG1sIGZ1cm9zZW1pZGUgMTAgbWcvbWwgaW5qZWN0aW9uOyBoeWRyb2NobG9yb3RoaWF6aWRlIDI1IG1nIG9yYWwgdGFibGV0OyBpYnVwcm9mZW4gMjAwIG1nIG9yYWwgdGFibGV0OyBtZXBlcmlkaW5lIGh5ZHJvY2hsb3JpZGUgNTAgbWcgb3JhbCB0YWJsZXQ7IGZ1cm9zZW1pZGUgNDAgbWcgb3JhbCB0YWJsZXQ7IHNvZGl1bSBmbHVvcmlkZSAwLjAyNzIgbWcvbWcgb3JhbCBnZWwKCiMgQXNzZXNzbWVudCBhbmQgUGxhbgoKCgojIyBQbGFuCgpUaGUgZm9sbG93aW5nIHByb2NlZHVyZXMgd2VyZSBjb25kdWN0ZWQ6Ci0gZWNob2NhcmRpb2dyYXBoeSAocHJvY2VkdXJlKQotIG1lZGljYXRpb24gcmVjb25jaWxpYXRpb24gKHByb2NlZHVyZSkKVGhlIGZvbGxvd2luZyBsYWIgcmVwb3J0cyB3ZXJlIGNvbXBsZXRlZDoKLSBoZWFydCBmYWlsdXJlIHRyYWNraW5nIHBhbmVsCi0gYmFzaWMgbWV0YWJvbGljIHBhbmVsIC0gYmxvb2QK"
    }
  ]
}
```

## Standard Radiology Report Structure To Embed

### IMPRESSION (also Summary or Conclusion) Section

This section provides a brief summary of the key findings and interpretations
from the diagnostic report. It should be concise and clear, highlighting the
most important and **actionable** information for the reader.

This will also appear in the FHIR as `DiagnosticReport.conclusion` or
`DiagnosticReport.conclusionCode` codable concept using, for example, SNOMED CT 
ICD-10/11 or LOINC codes.

### FINDINGS (Detailed Observations) Section

This section presents the detailed findings from the diagnostic report. It
should be organized in a logical manner, providing a comprehensive overview of
the observed abnormalities or abnormalities.

### Example Data Structures

```python
# 1. Discrete Finding Observation
finding_obs = {
    "resourceType": "Observation",
    "id": "obs-finding-1",
    "status": "final",
    "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "imaging"}]}],
    "code": {"coding": [{"system": "http://loinc.org", "code": "59776-5", "display": "Procedure findings"}]},
    "subject": {"reference": "Patient/123"},
    "valueString": "3.2 cm spiculated mass in the right upper lobe. No pleural effusion."
}

# 2. DiagnosticReport tying Findings (result) and Impression (conclusion) together
diagnostic_report = {
    "resourceType": "DiagnosticReport",
    "id": "dr-456",
    "status": "final",
    "code": {"coding": [{"system": "http://loinc.org", "code": "24627-2", "display": "Chest CT"}]},
    "subject": {"reference": "Patient/123"},
    "result": [
        {"reference": "Observation/obs-finding-1"}  # <-- FINDINGS
    ],
    "conclusion": "Suspicious right upper lobe pulmonary mass; recommend tissue sampling.",  # <-- IMPRESSION
    "conclusionCode": [
        {"coding": [{"system": "http://snomed.info/sct", "code": "309529002", "display": "Mass of lung"}]}
    ]
}
```

# Appendix

The DICOM Structured Report Template TID 1500 is related to Diagnostic Report 
and the following Python code example provides a help to describe how that
structure relate or even enhance the reporting. This is especially true for
Ultrasound reports where many measurements are made in the modality.

```python
import uuid
from typing import Any, List, Optional
import pydicom
from pydicom.dataset import Dataset
from fhir.resources.diagnosticreport import DiagnosticReport
from fhir.resources.observation import Observation
from fhir.resources.codeableconcept import CodeableConcept
from fhir.resources.coding import Coding
from fhir.resources.quantity import Quantity
from fhir.resources.identifier import Identifier
from fhir.resources.reference import Reference


# --- Helpers for DICOM Code Sequences & FHIR Types ---

def _extract_coding(code_seq: Optional[pydicom.Sequence]) -> Optional[Coding]:
    """Converts a DICOM Code Sequence item to a FHIR Coding."""
    if not code_seq or len(code_seq) == 0:
        return None
    item = code_seq[0]
    scheme_map = {
        "SCT": "http://snomed.info/sct",
        "LN": "http://loinc.org",
        "DCM": "http://dicom.nema.org/resources/ontology/DCM",
        "UCUM": "http://unitsofmeasure.org",
    }
    raw_scheme = getattr(item, "CodingSchemeDesignator", "")
    return Coding(
        system=scheme_map.get(raw_scheme, f"urn:oid:{raw_scheme}"),
        code=getattr(item, "CodeValue", None),
        display=getattr(item, "CodeMeaning", None),
    )


def _codeable_concept(coding: Optional[Coding], text: Optional[str] = None) -> CodeableConcept:
    return CodeableConcept(coding=[coding] if coding else None, text=text)


def _get_concept_code(item: Dataset) -> Optional[str]:
    seq = getattr(item, "ConceptNameCodeSequence", None)
    return seq[0].CodeValue if seq and len(seq) > 0 else None


# --- Core TID 1500 Mapper ---

def map_tid1500_to_fhir(ds: Dataset, patient_ref: str) -> dict[str, Any]:
    """
    Maps a DICOM SR TID 1500 dataset into a FHIR DiagnosticReport
    and a list of Observation resources (Measurement Groups + leaf Observations).
    """
    observations: List[Observation] = []
    top_level_refs: List[Reference] = []
    conclusion_texts: List[str] = []
    conclusion_codes: List[CodeableConcept] = []

    content_seq = getattr(ds, "ContentSequence", [])

    for top_item in content_seq:
        concept_code = _get_concept_code(top_item)

        # 1. Process Findings Container (DCM 121070)
        if concept_code == "121070" and hasattr(top_item, "ContentSequence"):
            for group_item in top_item.ContentSequence:
                # Look for Measurement Group (DCM 125007)
                if _get_concept_code(group_item) == "125007":
                    group_obs, child_obs_list = _parse_measurement_group(
                        group_item, patient_ref
                    )
                    observations.append(group_obs)
                    observations.extend(child_obs_list)
                    top_level_refs.append(Reference(reference=f"Observation/{group_obs.id}"))

        # 2. Process Conclusions / Impression Container (DCM 121077)
        elif concept_code == "121077" and hasattr(top_item, "ContentSequence"):
            for conc_item in top_item.ContentSequence:
                val_type = getattr(conc_item, "ValueType", "")
                if val_type == "TEXT":
                    conclusion_texts.append(conc_item.TextValue)
                elif val_type == "CODE":
                    coding = _extract_coding(getattr(conc_item, "ConceptCodeSequence", None))
                    if coding:
                        conclusion_codes.append(_codeable_concept(coding))

    # 3. Assemble Canonical DiagnosticReport
    report_coding = _extract_coding(getattr(ds, "ConceptNameCodeSequence", None))
    report = DiagnosticReport(
        id=str(uuid.uuid4()),
        status="final",
        category=[
            _codeable_concept(
                Coding(
                    system="http://terminology.hl7.org/CodeSystem/v2-0074",
                    code="RAD",
                    display="Radiology",
                )
            )
        ],
        code=_codeable_concept(report_coding, text="Measurement Report"),
        subject=Reference(reference=patient_ref),
        result=top_level_refs,
        conclusion="\n".join(conclusion_texts) if conclusion_texts else None,
        conclusionCode=conclusion_codes if conclusion_codes else None,
    )

    return {
        "diagnostic_report": report,
        "observations": observations,
    }


def _parse_measurement_group(
    group_item: Dataset, patient_ref: str
) -> tuple[Observation, List[Observation]]:
    """
    Parses a TID 1410/1411 Measurement Group into a parent grouping Observation
    and child Observations for quantitative NUMs and qualitative CODEs/TEXTs.
    """
    group_id = str(uuid.uuid4())
    identifiers: List[Identifier] = []
    body_site: Optional[CodeableConcept] = None
    finding_focus: Optional[CodeableConcept] = None
    child_observations: List[Observation] = []
    member_refs: List[Reference] = []

    for item in getattr(group_item, "ContentSequence", []):
        c_code = _get_concept_code(item)
        val_type = getattr(item, "ValueType", "")
        concept_coding = _extract_coding(getattr(item, "ConceptNameCodeSequence", None))

        # Tracking ID (DCM 112039) & Tracking UID (DCM 112040)
        if c_code == "112039" and val_type == "TEXT":
            identifiers.append(Identifier(system="urn:dicom:tracking-id", value=item.TextValue))
        elif c_code == "112040" and val_type == "UIDREF":
            identifiers.append(Identifier(system="urn:ietf:rfc:3986", value=f"urn:oid:{item.UID}"))

        # Finding Site (SCT 363698007 / SRT G-C0E3)
        elif c_code in ("363698007", "G-C0E3") and val_type == "CODE":
            site_coding = _extract_coding(getattr(item, "ConceptCodeSequence", None))
            body_site = _codeable_concept(site_coding)

        # Finding Category / Type (DCM 121071)
        elif c_code == "121071" and val_type == "CODE":
            finding_coding = _extract_coding(getattr(item, "ConceptCodeSequence", None))
            finding_focus = _codeable_concept(finding_coding)

        # Quantitative Measurement (NUM)
        elif val_type == "NUM":
            meas_seq = getattr(item, "MeasuredValueSequence", [])
            if meas_seq:
                m_val = meas_seq[0]
                unit_coding = _extract_coding(getattr(m_val, "MeasurementUnitsCodeSequence", None))
                child_obs = Observation(
                    id=str(uuid.uuid4()),
                    status="final",
                    code=_codeable_concept(concept_coding),
                    subject=Reference(reference=patient_ref),
                    valueQuantity=Quantity(
                        value=float(m_val.NumericValue),
                        unit=unit_coding.display if unit_coding else None,
                        system="http://unitsofmeasure.org",
                        code=unit_coding.code if unit_coding else None,
                    ),
                )
                child_observations.append(child_obs)
                member_refs.append(Reference(reference=f"Observation/{child_obs.id}"))

        # Qualitative Evaluation (CODE or TEXT)
        elif val_type in ("CODE", "TEXT"):
            child_obs = Observation(
                id=str(uuid.uuid4()),
                status="final",
                code=_codeable_concept(concept_coding),
                subject=Reference(reference=patient_ref),
            )
            if val_type == "CODE":
                val_coding = _extract_coding(getattr(item, "ConceptCodeSequence", None))
                child_obs.valueCodeableConcept = _codeable_concept(val_coding)
            else:
                child_obs.valueString = item.TextValue

            child_observations.append(child_obs)
            member_refs.append(Reference(reference=f"Observation/{child_obs.id}"))

    # Propagate Finding Site to child observations per FHIR Imaging profiles
    if body_site:
        for child in child_observations:
            child.bodySite = body_site

    group_obs = Observation(
        id=group_id,
        status="final",
        identifier=identifiers if identifiers else None,
        code=finding_focus
        or _codeable_concept(
            Coding(
                system="http://dicom.nema.org/resources/ontology/DCM",
                code="125007",
                display="Measurement Group",
            )
        ),
        subject=Reference(reference=patient_ref),
        bodySite=body_site,
        hasMember=member_refs if member_refs else None,
    )

    return group_obs, child_observations
```
