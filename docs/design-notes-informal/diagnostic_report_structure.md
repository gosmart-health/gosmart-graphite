# Diagnostic Report Structure Design Node

## Introduction

This design node outlines the structure of the diagnostic report, which is a crucial component of the diagnostic system. The diagnostic report provides detailed information about the diagnostic process, including the diagnostic results, the diagnostic context, and the diagnostic actions taken.

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
        if not any(ord(c) < 32 and c not in "\r\n\t\f\b" for c in decoded_sample):
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
    "profile": [ "http://hl7.org/fhir/us/core/StructureDefinition/us-core-diagnosticreport-note" ]
  },
  "status": "final",
  "category": [ {
    "coding": [ {
      "system": "http://loinc.org",
      "code": "34117-2",
      "display": "History and physical note"
    }, {
      "system": "http://loinc.org",
      "code": "51847-2",
      "display": "Evaluation + Plan note"
    } ]
  } ],
  "code": {
    "coding": [ {
      "system": "http://loinc.org",
      "code": "34117-2",
      "display": "History and physical note"
    }, {
      "system": "http://loinc.org",
      "code": "51847-2",
      "display": "Evaluation + Plan note"
    } ]
  },
  "subject": {
    "reference": "Patient/182539"
  },
  "encounter": {
    "reference": "Encounter/183748"
  },
  "effectiveDateTime": "2016-01-10T21:45:51-08:00",
  "issued": "2016-01-10T21:45:51.212-08:00",
  "performer": [ {
    "reference": "Practitioner/1989",
    "display": "Dr. Ted955 Reilly981"
  } ],
  "presentedForm": [ {
    "contentType": "text/plain; charset=utf-8",
    "data": "CjIwMTYtMDEtMTAKCiMgQ2hpZWYgQ29tcGxhaW50Ck5vIGNvbXBsYWludHMuCgojIEhpc3Rvcnkgb2YgUHJlc2VudCBJbGxuZXNzCkZsb3JyaWU0NTMgQWphODQ4IGlzIGEgODAgeWVhci1vbGQgbm9uaGlzcGFuaWMgd2hpdGUgZmVtYWxlLiBQYXRpZW50IGhhcyBhIGhpc3Rvcnkgb2YgZ2luZ2l2YWwgZGlzZWFzZSAoZGlzb3JkZXIpLCBtZWRpY2F0aW9uIHJldmlldyBkdWUgKHNpdHVhdGlvbiksIHVuZW1wbG95ZWQgKGZpbmRpbmcpLCBhY3V0ZSB2aXJhbCBwaGFyeW5naXRpcyAoZGlzb3JkZXIpLCB2aWN0aW0gb2YgaW50aW1hdGUgcGFydG5lciBhYnVzZSAoZmluZGluZyksIHZpcmFsIHNpbnVzaXRpcyAoZGlzb3JkZXIpLCBsaW1pdGVkIHNvY2lhbCBjb250YWN0IChmaW5kaW5nKSwgbm90IGluIGxhYm9yIGZvcmNlIChmaW5kaW5nKSwgZnVsbC10aW1lIGVtcGxveW1lbnQgKGZpbmRpbmcpLCBndW5zaG90IHdvdW5kIChkaXNvcmRlciksIGJ1bGxldCB3b3VuZCAoZGlzb3JkZXIpLCBnaW5naXZpdGlzIChkaXNvcmRlciksIHN0cmVzcyAoZmluZGluZyksIHNvY2lhbCBpc29sYXRpb24gKGZpbmRpbmcpLgoKIyBTb2NpYWwgSGlzdG9yeQpQYXRpZW50IGlzIHNpbmdsZS4gUGF0aWVudCBoYXMgbmV2ZXIgc21va2VkLgogUGF0aWVudCBpZGVudGlmaWVzIGFzIGhldGVyb3NleHVhbC4KClBhdGllbnQgY29tZXMgZnJvbSBhIG1pZGRsZSBzb2Npb2Vjb25vbWljIGJhY2tncm91bmQuCiBQYXRpZW50IGRpZCBub3QgZmluaXNoIGhpZ2ggc2Nob29sLgpQYXRpZW50IGN1cnJlbnRseSBoYXMgTWVkaWNhcmUuCgojIEFsbGVyZ2llcwpObyBLbm93biBBbGxlcmdpZXMuCgojIE1lZGljYXRpb25zCmxpc2lub3ByaWwgMTAgbWcgb3JhbCB0YWJsZXQ7IDEwIG1sIGZ1cm9zZW1pZGUgMTAgbWcvbWwgaW5qZWN0aW9uOyBoeWRyb2NobG9yb3RoaWF6aWRlIDI1IG1nIG9yYWwgdGFibGV0OyBpYnVwcm9mZW4gMjAwIG1nIG9yYWwgdGFibGV0OyBtZXBlcmlkaW5lIGh5ZHJvY2hsb3JpZGUgNTAgbWcgb3JhbCB0YWJsZXQ7IGZ1cm9zZW1pZGUgNDAgbWcgb3JhbCB0YWJsZXQ7IHNvZGl1bSBmbHVvcmlkZSAwLjAyNzIgbWcvbWcgb3JhbCBnZWwKCiMgQXNzZXNzbWVudCBhbmQgUGxhbgoKCgojIyBQbGFuCgpUaGUgZm9sbG93aW5nIHByb2NlZHVyZXMgd2VyZSBjb25kdWN0ZWQ6Ci0gZWNob2NhcmRpb2dyYXBoeSAocHJvY2VkdXJlKQotIG1lZGljYXRpb24gcmVjb25jaWxpYXRpb24gKHByb2NlZHVyZSkKVGhlIGZvbGxvd2luZyBsYWIgcmVwb3J0cyB3ZXJlIGNvbXBsZXRlZDoKLSBoZWFydCBmYWlsdXJlIHRyYWNraW5nIHBhbmVsCi0gYmFzaWMgbWV0YWJvbGljIHBhbmVsIC0gYmxvb2QK"
  } ]
}
```