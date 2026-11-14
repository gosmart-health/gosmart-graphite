# Designing the Graph RAG with GRAPHITE

## Why Graph RAG?

Graph RAG is a retrieval-augmented generation architecture that uses a knowledge graph to store and retrieve interconnected information. It improves upon standard vector RAG by combining semantic similarity with explicit structural relationships, enabling accurate, grounded responses across large clinical datasets.

In the GRAPHITE server, we aim to support not just semantic search, but also graph-based relationship traversal. This allows users to query across relationships between clinical and imaging entities rather than relying solely on vector similarity, enabling more accurate, explainable responses and intuitive exploration of connected data.

### End-to-End GraphRAG Workflow

Unlike naive multi-turn prompting where a user must manually follow up to ask for related records, GraphRAG traverses the graph automatically during the initial retrieval step before generating the first answer:

1. **Indexing (Pre-computation):** Before any user query occurs, entities (nodes) and relationships (edges) are extracted and stored in a graph database (Neo4j). Narrative nodes, edges, or community summaries are embedded as vectors for semantic search. For high-level sensemaking, clustering algorithms can group tightly connected nodes into communities with pre-generated summaries.
2. **Query & Anchor Retrieval:** When a user submits a query, the system converts it into a vector embedding (or extracts deterministic identifiers) to locate the top matching entry points—known as **anchor nodes**—in the graph.
3. **Automated Multi-Hop Graph Traversal:** The retriever automatically traverses outward from those anchor nodes (typically 1 to 3 hops along whitelisted relationship edges) to gather the surrounding neighborhood. For example, a semantic match on a `DiagnosticReport` node automatically pulls in linked `Observation`, `Encounter`, and `ImagingStudy` nodes without the user having to explicitly ask for each resource type.
4. **Context Assembly & Generation:** The retrieved subgraph—including node properties, relationship paths, and relevant summaries—is serialized into structured text (such as triples or JSON) and injected into the LLM prompt alongside the original query so the LLM can synthesize a grounded answer in a single turn.

### Advantages Over Standard Vector RAG

- **Multi-hop reasoning:** Standard vector search struggles when an answer requires connecting facts spread across isolated text chunks. Graph traversal follows explicit edges to bridge those gaps.
- **Global "sensemaking" queries:** Broad questions such as *"What are the main themes across all these reports?"* fail under standard top-$k$ vector retrieval, whereas GraphRAG answers them by querying pre-computed community summaries or hierarchical rollups.

---

## The Architecture

### Different Query Goals & Single Point of Entry

GRAPHITE uses different GraphRAG strategies depending on the user query:

- **Deterministic queries:** Finding specific imaging studies performed for a known patient, counting studies, or filtering by exact acquisition metadata, sometimes combined with natural-language boundaries such as clinical indications.
- **Open-ended / Cohort queries:** Searching across patients for specific symptoms, clinical conditions, or phenotypes.
- **Multi-step longitudinal investigations:** Comparing clinical states before and after an imaging event across multiple resource types.

A core requirement of GRAPHITE is to provide a **single point of query entry** without requiring the user to know or instruct the LLM on which GraphRAG strategy to use. Because no single database query approach fits all queries, GRAPHITE uses a **router pre-processing step** that analyzes the nature of the query and selects the appropriate GraphRAG strategy (or skips vector/graph traversal in favor of deterministic Cypher or standard RAG when appropriate).

---

### Neo4j Vector-plus-Cypher Traversal Patterns

In a unified clinical property graph combining **HL7 FHIR** (deep, patient-centric, reference-heavy) and **DICOM** (strictly hierarchical: `Patient → Study → Series → SOPInstance`, bridged via `ImagingStudy`), Neo4j supports three core vector-plus-Cypher retrieval architectures. Choosing the right pattern depends on whether the entry point is a patient-scoped clinical query or a cohort-wide semantic discovery query, and how supernode fan-out is controlled.

#### 1. Core Retrieval Patterns

| Pattern | Execution Order | Best Clinical Use Case | Strengths & Failure Modes |
| :--- | :--- | :--- | :--- |
| **Post-Retrieval Expansion (`VectorCypherRetriever`)** | 1. Global HNSW vector search on narrative nodes (`DiagnosticReport`, `DocumentReference`, `NoteChunk`)<br>2. Outward Cypher traversal from top-$k$ anchors | Cohort discovery, cross-patient similar case lookup, or open-ended clinical questions (*"Find chest CT reports mentioning ground-glass opacities and pull their acquisition protocols"*) | **Strength:** Fast global entry.<br>**Failure Mode:** Top-$k$ saturation—if one patient has 15 follow-up reports, they can consume the entire $k$ budget before filtering. |
| **Pre-Filtered Vector Scoring (Graph-First Candidate Scoping)** | 1. Cypher pattern match anchors on deterministic IDs (`Patient`, `Encounter`, `AccessionNumber`)<br>2. Exact `vector.similarity.cosine()` over the constrained subgraph | Single-patient chart review, longitudinal tracking, or encounter-scoped queries (*"Summarize prior MRI findings for Patient X before this oncology consult"*) | **Strength:** Zero cross-patient leakage; 100% recall within the patient's subgraph; low latency when candidate set is small.<br>**Failure Mode:** Scales linearly with candidate count—do not run without an anchor ID. |
| **In-Index Hybrid Filtering (Cypher 25 `SEARCH ... WHERE`)** | 1. HNSW vector traversal pruned simultaneously by indexed metadata predicates (`modality = 'MR'`, `status = 'final'`, `effectiveDate`)<br>2. Targeted neighborhood expansion | Modality- or timeframe-restricted semantic search (*"Recent brain MRIs with acute infarct findings"*) | **Strength:** Avoids post-filter recall collapse while preserving HNSW speed.<br>**Failure Mode:** Filter properties must be primitive properties on the embedded node itself, not on multi-hop neighbors. |

#### 2. Query Structures for Unified FHIR + DICOM Graphs

##### Pattern A: Post-Retrieval Expansion (Report Anchor → FHIR Context + DICOM Acquisition)

Use this pattern when anchoring on embedded radiology report impressions (`DiagnosticReport` or `Observation` text chunks) and bridging across `ImagingStudy` into DICOM Series metadata:

```cypher
// 1. Semantic entry on DiagnosticReport impression embeddings
CALL db.index.vector.queryNodes('report_impression_idx', $top_k, $query_vec)
YIELD node AS report, score
WHERE score >= 0.78

// 2. Traverse upward to Encounter/Condition (FHIR) and downward to DICOM Series
MATCH (patient:Patient)<-[:SUBJECT]-(report)
OPTIONAL MATCH (report)-[:ENCOUNTER]->(enc:Encounter)-[:REASON_REFERENCE]->(cond:Condition)
OPTIONAL MATCH (report)-[:IMAGING_STUDY]->(study:ImagingStudy)-[:HAS_SERIES]->(series:DicomSeries)
WHERE series.modality IN ['CT', 'MR']

RETURN patient.mrn AS mrn,
       report.issued AS reportDate,
       score,
       collect(DISTINCT cond.codeText) AS clinicalIndications,
       collect(DISTINCT {
         seriesUID: series.seriesInstanceUID,
         modality: series.modality,
         bodyPart: series.bodyPartExamined,
         protocol: series.protocolName,
         sliceThickness: series.sliceThickness
       }) AS dicomSeries
```

##### Pattern B: Pre-Filtered Vector Scoring (Patient-Scoped Subgraph)

Use this pattern when the clinical workflow already knows the `Patient` or `Encounter`. Traversing first and scoring inline eliminates any risk of cross-patient vector collisions:

```cypher
// 1. Deterministic anchor on Patient -> DiagnosticReports & DocumentReferences
MATCH (p:Patient {id: $patient_id})<-[:SUBJECT]-(doc)
WHERE (doc:DiagnosticReport OR doc:DocumentReference)
  AND doc.embedding IS NOT NULL
  AND doc.issued >= date($lookback_start)

// 2. Exact cosine similarity restricted strictly to this patient's records
WITH p, doc, vector.similarity.cosine(doc.embedding, $query_vec) AS score
WHERE score >= 0.72
ORDER BY score DESC
LIMIT 5

// 3. Pull linked DICOM ImagingStudy + Quantitative FHIR Observations
OPTIONAL MATCH (doc)-[:RESULT]->(obs:Observation)
OPTIONAL MATCH (doc)-[:IMAGING_STUDY]->(study:ImagingStudy)-[:HAS_SERIES]->(s:DicomSeries)
RETURN doc.id AS resourceId,
       doc.codeText AS studyType,
       score,
       collect(DISTINCT obs.codeText + ': ' + toString(obs.valueQuantity) + ' ' + obs.unit) AS labContext,
       collect(DISTINCT {studyUID: study.studyInstanceUID, seriesCount: count(s)}) AS imagingContext
```

#### 3. Hop-Depth Trade-Offs & Supernode Mitigations

In FHIR/DICOM graphs, hop count is non-linear in cost because clinical schemas mix tree hierarchies (DICOM) with hub-and-spoke networks (FHIR):

- **1 Hop (Direct Resource Neighborhood):**
  - *Examples:* `DiagnosticReport → Observation` (`:RESULT`), `DiagnosticReport → ImagingStudy` (`:IMAGING_STUDY`), `DicomStudy → DicomSeries` (`:HAS_SERIES`).
  - *Trade-off:* Lowest latency and highest signal-to-noise ratio for LLM context. Safe to pull properties without aggressive pruning.
- **2–3 Hops (Cross-Standard Bridge):**
  - *Examples:* `DiagnosticReport → ImagingStudy → DicomSeries`, or `Observation → Encounter → Condition` / `MedicationRequest`.
  - *Trade-off:* The sweet spot for clinical GraphRAG. Bridges unstructured narrative impressions with structured DICOM acquisition parameters (e.g., contrast agent, slice thickness, field strength) and clinical indications. Requires explicit relationship-type whitelisting (`[:IMAGING_STUDY|HAS_SERIES]`) rather than untyped `-[*1..3]-` variable-length paths.
- **4+ Hops & Supernode Traps (Never Traverse Blindly):**
  - *The Trap:* In FHIR, `Patient`, `Practitioner`, and `Organization` are dense supernodes. An untyped 3- or 4-hop traversal (`(report)-[*1..4]-(related)`) hits `Patient` on hop 1 and explodes across every historical `Observation`, `Claim`, `MedicationAdministration`, and `SOPInstance` (individual DICOM slices) in the database.
  - *Mitigations:*
    - **Stop at `DicomSeries`:** Never traverse down to `SOPInstance` (hop 4 from `DiagnosticReport`) during RAG retrieval; aggregate instance counts or key-image UIDs onto the `DicomSeries` node at ingestion time.
    - **Block Supernode Backtracking:** Never traverse through a `Patient` node to find "other reports for the same patient" inside an unanchored multi-hop pattern unless explicitly scoped by `Encounter` or a temporal window.
    - **Use `SHORTEST` / Quantified Path Patterns:** When linking longitudinal events (e.g., tracking a lesion across serial studies), bind explicit `[:FOLLOWS_UP]` or temporal edges rather than multi-hop traversals through `Patient`.

---

### Router Pre-Processing (Routed GraphRAG)

To select the optimal GraphRAG strategy, GRAPHITE analyzes the nature of the incoming query before executing database retrieval.

#### 1. Implementation Approaches

| Approach | How It Works | Best For |
| :--- | :--- | :--- |
| **Fast Classifier / Small Model (Recommended for high-throughput)** | A lightweight encoder (e.g., ModernBERT / DeBERTa) or fast lite LLM (e.g., `gemini-3.8-flash-lite`) outputs a strict structured route enum before touching Neo4j. | High-volume endpoints where predictable routing into fixed retrieval templates is sufficient. |
| **LLM Tool-Calling Router (e.g., Neo4j `ToolsRetriever`)** | Each retrieval strategy (Vector+Cypher, Patient-Scoped Subgraph, Text2Cypher) is registered as a tool with a schema; the LLM selects one or multiple tools to invoke. | Complex multi-part questions that require combining two strategies (e.g., pulling quantitative lab counts and narrative radiology impressions). |
| **Deterministic Guard + LLM Fallback** | Regex or metadata rules catch explicit IDs (`Patient/123`, `AccessionNumber`, MRNs) first; only ambiguous natural-language queries hit the LLM router. | Clinical systems where known patient/encounter context should immediately lock into a patient-scoped subgraph without risking LLM misrouting. |

#### 2. Clinical Routing Taxonomy

Rather than letting an LLM generate arbitrary Cypher from scratch (which carries syntax-error and supernode fan-out risks), the classifier maps user intent into strictly typed Pydantic route schemas that trigger parameterized Cypher templates:

- **`PATIENT_CHART_QA` (Graph-First Pre-Filtered Vector):**
  - *Trigger:* Query references a specific patient, MRN, encounter, or accession ID.
  - *Action:* Anchor on `Patient` / `Encounter` first, then run cosine similarity strictly within that patient's subgraph.
- **`COHORT_SEMANTIC_DISCOVERY` (Post-Retrieval Vector + Cypher Expansion):**
  - *Trigger:* Open-ended clinical similarity or phenotype search across patients (*"Cases with pulmonary nodules and prior contrast CT"*).
  - *Action:* Global HNSW index search on `DiagnosticReport` / `NoteChunk`, followed by 1–2 hop expansion into `ImagingStudy` and `DicomSeries`.
- **`STRUCTURED_AGGREGATION` (Parameterized Cypher / Read-Only Text2Cypher):**
  - *Trigger:* Counting, filtering, or listing deterministic attributes (*"List all CT series with slice thickness < 1.5mm from last month"*).
  - *Action:* Pure Cypher execution with `LIMIT` and `EXPLAIN`/read-only guards—skip vector embeddings entirely.
- **`GLOBAL_THEME_SUMMARY` (Community / Map-Reduce Retrieval):**
  - *Trigger:* Broad synthesis across many records (*"What are the most common incidental findings across outpatient chest CTs?"*).
  - *Action:* Query pre-computed community summaries or hierarchical report rollups rather than top-$k$ chunk retrieval.
- **`MULTI_STEP_INVESTIGATION` (Bounded Agentic Loop):**
  - *Trigger:* Temporal comparisons across events, sequential study correlation, or dependent multi-hop lookups.
  - *Action:* Hand off to a bounded agentic retriever (see below).

#### 3. Practical Guardrails for the Router

- **Enforce Structured Outputs:** Use Pydantic v2 / JSON Schema strict mode on the routing step so the model can only return a valid `route_type` and extracted filter parameters (`modality`, `date_range`, `patient_id`, `semantic_query`).
- **Separate Routing from Generation:** Do not ask a single prompt to classify, write Cypher, and answer the question in one pass. A dedicated lightweight classification call keeps prompts small, avoids context pollution, and lets you log routing accuracy independently.
- **Support Hybrid / Multi-Route Execution:** For compound clinical questions (*"Compare Patient X's latest MRI tumor measurements against similar glioblastoma cases"*), allow the orchestrator to execute `PATIENT_CHART_QA` and `COHORT_SEMANTIC_DISCOVERY` concurrently via `asyncio.gather()`.

---

### Routed GraphRAG vs. Agentic GraphRAG

A single upfront classifier implements **Routed GraphRAG** (a deterministic state machine / DAG). **Agentic GraphRAG** begins when the LLM controls an iterative **Reason → Act → Observe** loop over the graph:

| Dimension | Routed GraphRAG (Single Upfront Classifier) | Agentic GraphRAG (Autonomous Loop) |
| :--- | :--- | :--- |
| **Control Flow** | One-shot DAG: Classify → Run Cypher template → Generate answer. | Iterative Loop: Plan → Query graph → Inspect subgraph → Decide next hop or tool → Synthesize. |
| **Failure Recovery** | If the chosen Cypher query returns 0 rows or incomplete context, the generator must make do with missing data. | If an `ImagingStudy` lacks a linked `Observation`, the agent notices the gap, reformulates a broader query, or checks a secondary FHIR endpoint. |
| **Multi-Step Planning** | Executes 1 (or parallel) pre-defined retrieval paths. | Decomposes a compound clinical question into sequential sub-queries where step 2 depends on node IDs or timestamps discovered in step 1. |
| **Latency & Cost** | Predictable (router call + DB round-trip + generation). | Variable (multiple LLM turns and graph queries per question). |

#### Clinical Workflow Trade-Offs

- **Where Routed GraphRAG is sufficient:**
  - *"Pull the last 3 chest CT reports and slice thicknesses for Patient X."*
  - The path is known upfront (`Patient → DiagnosticReport → ImagingStudy → DicomSeries`). One classification step dispatches a parameterized Cypher query with deterministic latency and zero risk of runaway loops.
- **Where Agentic GraphRAG is required:**
  - *"Did Patient X's renal function decline after their contrast-enhanced abdominal CT, and did any subsequent ultrasound show hydronephrosis?"*
  - An agentic retriever treats the Neo4j graph (and any MCP tools) as an environment to explore:
    1. **Act:** Finds the contrast CT `ImagingStudy` and extracts its `started` timestamp.
    2. **Observe & Re-plan:** Uses that timestamp as a parameter to query `Observation` nodes (`eGFR` / `Creatinine`) before vs. after the scan date.
    3. **Act:** Queries `DiagnosticReport` nodes after that date filtered to modality `US` (ultrasound) to check for hydronephrosis.
    4. **Synthesize:** Combines the timeline across FHIR labs and DICOM studies into a grounded answer.

#### Practical Architecture Rule

Start with **Routed GraphRAG** (a fast classifier dispatching into safe, bounded Cypher templates) for ~80% of standard clinical lookups. Wrap those exact same parameterized Cypher retrievers inside a **bounded agent loop** (e.g., `max_iterations = 4`) only when the router classifies a query as `MULTI_STEP_INVESTIGATION`. This keeps simple lookups fast and deterministic while giving complex longitudinal questions multi-hop reasoning without granting the LLM raw write or unbounded traversal access to Neo4j.

---

## Reference Implementation: Genkit Python Router

GRAPHITE uses **Firebase Genkit** (`genkit` + `genkit-google-genai`) with Pydantic v2 for LLM orchestration. When using `gemini-3.8-flash-lite` (or `gemini-3.8-flash`) for routing, the key to reliability and low latency is keeping the system instruction concise, priority-ordered, and mutually exclusive while enforcing strict JSON decoding via `output_schema` and setting `thinking_level="MINIMAL"`.

### 1. System Instruction & Pydantic v2 Router Schema (`router.py`)

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from genkit import Genkit
from genkit_google_genai import GeminiConfig, GoogleAI

ai = Genkit(plugins=[GoogleAI()])


class RetrievalRoute(str, Enum):
    PATIENT_CHART_QA = "PATIENT_CHART_QA"
    COHORT_SEMANTIC_DISCOVERY = "COHORT_SEMANTIC_DISCOVERY"
    STRUCTURED_AGGREGATION = "STRUCTURED_AGGREGATION"
    MULTI_STEP_INVESTIGATION = "MULTI_STEP_INVESTIGATION"


class ExtractedFilters(BaseModel):
    patient_id: Optional[str] = Field(
        default=None,
        description="Explicit FHIR Patient ID, MRN, or AccessionNumber if mentioned.",
    )
    modalities: list[str] = Field(
        default_factory=list,
        description="DICOM modality codes if specified (e.g., ['CT', 'MR', 'US', 'DX']).",
    )
    lookback_days: Optional[int] = Field(
        default=None,
        description="Relative timeframe converted to days (e.g., 'last 6 months' -> 180).",
    )
    semantic_query: str = Field(
        description="Clean clinical search phrase stripped of conversational filler, optimized for dense embedding lookup.",
    )


class ClinicalQueryPlan(BaseModel):
    route: RetrievalRoute = Field(
        description="Primary GraphRAG execution strategy for this query."
    )
    filters: ExtractedFilters
    requires_dicom_series_metadata: bool = Field(
        description="True if answering requires DICOM acquisition details (slice thickness, protocol, contrast, series count)."
    )
    reasoning: str = Field(
        description="One-sentence justification for the selected route."
    )


ROUTER_SYSTEM_PROMPT = """
You are a clinical GraphRAG query router for a unified HL7 FHIR + DICOM Neo4j knowledge graph.
Classify the user's request into exactly ONE execution route and extract structured filter parameters.
Do NOT answer the clinical question.

### Routing Rules (Evaluate in Priority Order)
1. MULTI_STEP_INVESTIGATION:
   - Choose when the question requires comparing events across time (e.g., "before vs. after contrast"), correlating sequential studies, or chaining two dependent lookups (e.g., finding a study date first, then checking subsequent lab values).
2. PATIENT_CHART_QA:
   - Choose when the query targets a single known patient, MRN, encounter, or accession number (e.g., "Summarize Patient 1234's prior chest CTs").
3. STRUCTURED_AGGREGATION:
   - Choose when the query asks for counts, lists, or deterministic metadata filtering without narrative semantic matching (e.g., "How many MRIs with slice thickness < 2mm were performed in 2026?").
4. COHORT_SEMANTIC_DISCOVERY:
   - Choose for open-ended clinical phenotype or pathology searches across multiple patients (e.g., "Find cases with ground-glass opacities and pull their CT protocols").

### Extraction Rules
- Normalize imaging modalities to standard DICOM codes: CT, MR, US, XR/DX, PT, NM.
- Strip conversational phrasing from `semantic_query` so it contains only clinical concepts, anatomy, findings, or lab names.
- Never invent patient IDs or accession numbers; leave `patient_id` null if not explicitly provided.
""".strip()
```

### 2. Genkit Flow Using `gemini-3.8-flash-lite` (`flow.py`)

```python
@ai.flow()
async def route_clinical_query(user_query: str) -> ClinicalQueryPlan:
    response = await ai.generate(
        # Swap between 'gemini-3.8-flash-lite' (lowest latency) and 'gemini-3.8-flash'
        model=GoogleAI.gemini("gemini-3.8-flash-lite"),
        system=ROUTER_SYSTEM_PROMPT,
        prompt=user_query,
        output_schema=ClinicalQueryPlan,
        config=GeminiConfig(
            temperature=0.0,
            thinking_level="MINIMAL",
            timeout=15,
        ),
    )

    if response.output is None:
        # Safe fallback if structured parsing fails
        return ClinicalQueryPlan(
            route=RetrievalRoute.COHORT_SEMANTIC_DISCOVERY,
            filters=ExtractedFilters(semantic_query=user_query),
            requires_dicom_series_metadata=False,
            reasoning="Fallback to default semantic discovery.",
        )

    return response.output
```

### 3. Design Rationale for Lite Models

- **Priority-ordered rules:** Lighter models (`flash-lite`) can wobble when two categories overlap (e.g., a patient-specific query that also asks for a temporal comparison). Numbering the rules in strict precedence order resolves ambiguity immediately.
- **Negative constraint (`Do NOT answer...`):** Prevents the model from spending output tokens summarizing clinical knowledge before emitting the JSON fields.
- **Normalized `semantic_query` extraction:** Having the router strip conversational filler (e.g., *"Can you pull up any reports where..."* → *"pulmonary embolism filling defect"*) improves downstream cosine similarity against `DiagnosticReport` impression embeddings without needing a second LLM call.
- **Explicit `requires_dicom_series_metadata` boolean:** Lets the Neo4j Cypher builder skip the `(:ImagingStudy)-[:HAS_SERIES]->(:DicomSeries)` join when the user only asked about FHIR labs or report impressions, saving 1–2 hops on every non-imaging query.
