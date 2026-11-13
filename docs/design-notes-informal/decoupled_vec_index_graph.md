# Decoupled Vector Index Graph Pattern Design

## Why Do We Need This?

Vector embeddings are dense, high-dimensional representations that carry substantial data, so we need an efficient way to store and retrieve them. The decoupled vector index graph pattern addresses this by separating embedding vectors from their target domain nodes.

Rather than storing vectors directly on target nodes—which may already hold large payloads and many properties—decoupling them keeps domain nodes lightweight, improves storage and retrieval efficiency, and simplifies data management.

In a graph database, this pattern provides a flexible and scalable way to manage embeddings while preserving all domain relationships between nodes. In addition, vector representations are tightly coupled to specific embedding models, which evolve over time. Separating vectors into dedicated nodes allows us to re-embed, version, or swap models without modifying target domain nodes or disrupting the underlying graph topology.

## Architecture

### Embedding Nodes

Embedding nodes should inherit from `AuditableNode`.

#### Properties

In addition to the fields inherited from `AuditableNode`, each Embedding node should define:

- **Label**: `:EMBEDDING`
- **`modelTag`**: System-specific short string identifying the embedding model used.
- **`size`**: Dimensionality (length) of the vector.
- **`vector`**: L2-normalized vector representation (an array of floats), indexed by a vector index.
- **`targetFieldTag`**: Tag identifying the field on the target node that the vector represents.

### Relationships — `:EMBEDS`

The `:EMBEDS` relationship should inherit from `AuditableRelation`.

- **`aspect`**: Tag identifying the aspect of the target node associated with the vector (for example, `IMPRESSION` or `FINDINGS`).

## Specific Vectorization of `DiagnosticReport` Nodes

A primary use case for semantic search on `DiagnosticReport` nodes is enabling natural-language 
search over the narrative text of diagnostic reports.

Full reports can exceed an embedding model's context window, and embedding an entire multi-section 
document as a single vector risks diluting key clinical signals. To avoid this, we split each 
report into its relevant clinical sections and generate embeddings per section.


#### Notable Fields

**` DiagnosticReport.conclusion`: Holds the narrative text of the conclusion. 
This can be embedded.
** `DiagnosticReport.conclusionCode`: Hold the SNOMED CT, ICD, LOINC codes of 
   the conclusion.
** `DiagnosticReport.result`


In FHIR, plain-text reports are typically Base64-encoded inside the `presentedForm` array, as shown below:

```text
"presentedForm": [ {
    "contentType": "text/plain; charset=utf-8",
    "data": "CjIwMTYtMDEtMTAKCiMgQ2hpZW..."
  } ]
```

Assuming the attachment is plain text rather than a PDF or another binary format, we look for the `text/plain` content type and decode the Base64-encoded `data` field to obtain the raw report text.

### Sectional Embedding Generation

Given the plain text of a diagnostic (radiology) report, we split it into canonical sections—following the standard structure of radiology reports—to prevent semantic dilution. (Initial heading patterns target English reports; multilingual support will be considered later.)

The following example demonstrates how to perform this parsing:

```python
from dataclasses import dataclass, field
import re
from typing import Literal
from pydantic import BaseModel, Field

CanonicalSection = Literal[
    "INDICATION",
    "TECHNIQUE",
    "COMPARISON",
    "FINDINGS",
    "IMPRESSION",
]


class ParsedReport(BaseModel):
    indication: str | None = Field(
        default=None,
        description="Clinical history or reason for exam, verbatim.",
    )
    technique: str | None = Field(
        default=None,
        description="Imaging protocol or technique, verbatim.",
    )
    comparison: str | None = Field(
        default=None,
        description="Prior comparison studies, verbatim.",
    )
    findings: str | None = Field(
        default=None,
        description="Detailed radiological observations, verbatim.",
    )
    impression: str | None = Field(
        default=None,
        description="Final diagnostic conclusion or opinion, verbatim.",
    )


@dataclass(slots=True)
class InstitutionParsingProfile:
    institution_id: str
    heading_patterns: dict[CanonicalSection, re.Pattern[str]] = field(default_factory=dict)


DEFAULT_HEADING_PATTERNS: dict[CanonicalSection, re.Pattern[str]] = {
    "INDICATION": re.compile(
        r"^(?:CLINICAL\s+)?(?:HISTORY|INDICATION|REASON\s+FOR\s+(?:EXAM|STUDY))[:\s-]*$",
        re.IGNORECASE,
    ),
    "TECHNIQUE": re.compile(
        r"^(?:TECHNIQUE|PROTOCOL|PROCEDURE)[:\s-]*$",
        re.IGNORECASE,
    ),
    "COMPARISON": re.compile(
        r"^(?:COMPARISON|PRIOR\s+STUDIES)[:\s-]*$",
        re.IGNORECASE,
    ),
    "FINDINGS": re.compile(
        r"^(?:FINDINGS|OBSERVATIONS|REPORT)[:\s-]*$",
        re.IGNORECASE,
    ),
    "IMPRESSION": re.compile(
        r"^(?:IMPRESSION|CONCLUSION|OPINION|DIAGNOSIS|SUMMARY)[:\s-]*$",
        re.IGNORECASE,
    ),
}

DEFAULT_PROFILE = InstitutionParsingProfile(
    institution_id="default",
    heading_patterns=DEFAULT_HEADING_PATTERNS,
)

INLINE_HEADER_RE = re.compile(
    r"^(CLINICAL\s+HISTORY|INDICATION|TECHNIQUE|COMPARISON|FINDINGS|IMPRESSION|CONCLUSION|OPINION):\s*(.+)$",
    re.IGNORECASE,
)


def _map_inline_token(header_token: str) -> CanonicalSection:
    upper = header_token.upper()
    if "HISTORY" in upper or "INDICATION" in upper:
        return "INDICATION"
    if "TECHNIQUE" in upper:
        return "TECHNIQUE"
    if "COMPARISON" in upper:
        return "COMPARISON"
    if "FINDINGS" in upper:
        return "FINDINGS"
    return "IMPRESSION"


def parse_with_regex_profile(
    raw_text: str,
    profile: InstitutionParsingProfile = DEFAULT_PROFILE,
) -> tuple[ParsedReport, bool]:
    """
    Deterministic Tier 1 parser: splits narrative report lines using facility-specific regexes.
    Returns (ParsedReport, is_complete).
    """
    buckets: dict[CanonicalSection, list[str]] = {}
    current_section: CanonicalSection | None = None

    for raw_line in raw_text.splitlines():
        trimmed = raw_line.strip()
        if not trimmed:
            continue

        # 1. Check standalone section heading lines
        matched_section: CanonicalSection | None = None
        for section, pattern in profile.heading_patterns.items():
            if pattern.match(trimmed):
                matched_section = section
                break

        if matched_section is not None:
            current_section = matched_section
            buckets.setdefault(current_section, [])
            continue

        # 2. Check inline headers (e.g., "IMPRESSION: No acute intracranial abnormality.")
        if inline_match := INLINE_HEADER_RE.match(trimmed):
            current_section = _map_inline_token(inline_match.group(1))
            buckets.setdefault(current_section, [])
            if remainder := inline_match.group(2).strip():
                buckets[current_section].append(remainder)
            continue

        if current_section is not None:
            buckets[current_section].append(trimmed)

    sections = ParsedReport(
        indication="\n".join(buckets["INDICATION"]).strip() or None
        if "INDICATION" in buckets
        else None,
        technique="\n".join(buckets["TECHNIQUE"]).strip() or None
        if "TECHNIQUE" in buckets
        else None,
        comparison="\n".join(buckets["COMPARISON"]).strip() or None
        if "COMPARISON" in buckets
        else None,
        findings="\n".join(buckets["FINDINGS"]).strip() or None
        if "FINDINGS" in buckets
        else None,
        impression="\n".join(buckets["IMPRESSION"]).strip() or None
        if "IMPRESSION" in buckets
        else None,
    )

    is_complete = bool(sections.impression and sections.findings)
    return sections, is_complete
```

## Granular Per-Resource Vector Nodes

Using `Observation` as an example, here is why per-resource vector nodes are preferred over aggregating all observations into a single text blob:

> ### Why Concatenating All Observations into a Single String Is Not Recommended
>
> - **Semantic Dilution & "Lost in the Middle"**: An encounter with 30 observations (labs, vitals, social history, findings) dilutes individual signals. A query for `"elevated diastolic pressure"` gets drowned out by unrelated lab panels and notes.
> - **Loss of Granular Clinical Context**: An `Observation` is not just free text—it carries structured metadata such as status (e.g., `preliminary`, `amended`), interpretation (e.g., `HIGH`, `CRITICAL`), numeric values with units (`150 mmHg`), and timestamps. Naive concatenation obscures these structured clinical semantics.
> - **Update Costs**: If a single observation is added to or amended in an `Encounter`, you would have to re-read every observation in that encounter and regenerate the entire concatenated embedding.

**Text to Embed**: Form a structured sentence rather than using raw pipe concatenation, for example:
`"Observation: Blood Pressure. Value: 150/95 mmHg. Interpretation: High. Note: Patient reports morning dizziness."`

```text
(:Patient)-[:HAS_ENCOUNTER]->(:Encounter)-[:HAS_OBSERVATION]->(:Observation)
                                                                   ▲
                                                                   │ :INDEXES
                                                    (:ObservationVectorIndex)
```

#### Query Example: Returning Related Encounters from an Observation Search

```cypher
CALL db.index.vector.queryNodes('observation_vector_index', 10, $queryVector)
     YIELD node AS vecNode, score
     MATCH (vecNode)<-[:INDEXES]-(obs:Observation)<-[:HAS_OBSERVATION]-(enc:Encounter)<-[:HAS_ENCOUNTER]-(p:Patient)
     RETURN p.id, enc.id, obs.codeText, obs.valueString, score
```

- Results may need to be deduplicated by patient and encounter.
