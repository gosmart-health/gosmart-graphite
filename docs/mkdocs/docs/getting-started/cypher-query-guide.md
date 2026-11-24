# Cypher Query Guide

## Introduction

This guide describes the GRAPHITE Neo4j property graph conventions and provides practical Cypher query examples for exploring indexed DICOM and FHIR data.

This is not a general Cypher reference — it assumes you can read Cypher and have a basic understanding of property graph databases. For an overview of how DICOM and FHIR nodes relate in the graph, see [How the data fits together](../index.md#how-the-data-fits-together).

### Running Cypher Queries

You can execute read queries directly from the CLI (see [First Steps](first-steps.md#3-query-the-graph)) or from Neo4j Browser / `cypher-shell`:

```bash
uv run gosmart-graphite query "MATCH (p:Patient) RETURN p.patient_id LIMIT 10"
```

## Core Data Entity Schema

Most graph nodes and relationships inherit from `Auditable` (`AuditableNode` / `AuditableRelation`) and share the following common properties:

| Property | Type | Description |
|:---------|:-----|:------------|
| `id` | `string` | Deterministic UUIDv5 identifier computed from the entity's identity subset (enforces idempotency). |
| `Action` | `string` | Auditable action taken: `CREATE`, `UPDATE`, `DELETE`, or `RESTORE`. |
| `ActedAt` | `datetime` | Timestamp of the action, normalized to UTC. |
| `ActedBy` | `string` | Identifier of the user, service, or agent that performed the action (defaults to `system`). |
| `CreatedAt` | `datetime` | Creation timestamp, normalized to UTC. |
| `UpdatedAt` | `datetime` | Last update timestamp, normalized to UTC. |
| `ExpiresAt` | `datetime` | Optional expiration timestamp (`null` implies non-expiring). |
| `test_id` | `string` | Optional ephemeral test identifier. Non-null indicates test-generated data. |

## Soft Deletion Semantics — The `NOT_` Relationship

Related nodes indicate soft deletion by prefixing the original relationship type with `NOT_`.

Generally, once a node is created it is treated as immutable; updates and deletions are implemented non-destructively by cloning the node and swapping the relationship type (see also [Immutability & soft deletion](../configuration/index.md#immutability-soft-deletion-not_-semantics)).

For example, if an active relationship is `:HAS_STUDY`, its soft-deleted counterpart is `:NOT_HAS_STUDY`.

!!! important "Soft Deletion Guarantees"
    - When a soft deletion is performed, the existing node or relationship is **not** removed from the graph. Instead, the node is preserved/cloned and the relationship type is switched to its `NOT_` prefixed counterpart.
    - Multiple soft-deleted nodes or relationships may exist in the graph, preserving the complete audit history of deletions and updates.
    - When writing Cypher queries for active records, match explicit active relationship types (e.g., `[:HAS_STUDY]`, `[:SUBJECT]`) rather than untyped wildcards that would also traverse `[:NOT_*]` edges.

## FHIR Nodes

FHIR nodes inherit from `FHIRDomainResourceNode(AuditableNode)`. In addition to the `Auditable` fields above, every FHIR node carries the following base properties:

| Property | Description |
|:---------|:------------|
| **Node Label** | Matches the FHIR resource type — for example `Encounter`, `Observation`, `Condition`, `DiagnosticReport`, `ImagingStudy`, `ServiceRequest`, `Procedure`, `Practitioner`, `Organization`, `Location`. **Exception:** FHIR Patient nodes use the label `FHIR_Patient` to distinguish them from DICOM `:Patient` nodes. |
| `uid` | Internal unique ID assigned at creation (defaults to `fhir:<resource_id>`, or a source-provided canonical URI such as `urn:uuid:...`). |
| `resource_id` | Source FHIR server resource ID. For example, if `resource_id` is `12345` on a `FHIR_Patient` node, the upstream resource is `Patient/12345`. |
| `status` | Lifecycle status copied from the FHIR resource (e.g., `active`, `final`, `cancelled`, `entered-in-error`). |
| `resource` | Raw JSON string of the received FHIR resource (inline when ≤ 4 KB, or stripped of large binary attachments). |
| `resourceUri` | External storage URI (`file://`, `s3://`, `gs://`, `az://`) when the raw FHIR JSON exceeds 4 KB. |

!!! info "Why 4 KB?"
    The FHIR specification allows resources up to 1 MB or more, whereas Neo4j query performance depends heavily on page-cache memory efficiency. When a raw FHIR resource exceeds 4 KB (a common OS/database page size), the full payload is offloaded to object/file storage and referenced via `resourceUri`.

    The inline `resource` property may hold a stripped version of the raw resource with large embedded PDF or image payloads removed.

### FHIR Node Properties

Frequently queried FHIR fields are promoted to top-level node properties and backed by Neo4j indexes so Cypher queries avoid parsing raw JSON.

#### Coded Value Representation

Coded values follow the FHIR Coding search macro format stored as string arrays (`system|code|display`):

```json
["http://loinc.org|4544-3|Hematocrit [Volume Fraction] of Blood by Automated count"]
```

#### Parallel Arrays for Codes, Numeric Values, and Units

Because Cypher cannot natively index or filter arbitrarily nested JSON structures, `Observation` and `DiagnosticReport` nodes expose synchronized parallel arrays indexed by position:

- `codes`: Array of Coding macro strings (`system|code|display`) synchronized by index with `numericValues` and `units` (in addition to `code`, which holds the primary observation/report coding macros).
- `numericValues`: Array of floating-point numeric values aligned with `codes`.
- `units`: Array of unit Coding macro strings aligned with `codes` and `numericValues`.

## Query Examples

### Find Specific Observations

#### 1. Match Observations by LOINC Code

Find all Erythrocyte Distribution Width (`LOINC 788-0`) observations in the graph:

```cypher
MATCH (obs:Observation)
WHERE any(item IN obs.code WHERE item STARTS WITH 'http://loinc.org|788-0')
RETURN obs
```

#### 2. Join Observations with `FHIR_Patient` and Filter by Numeric Value

Expand the query to return the patient name, observation text, and value for observations with a numeric value greater than `12.0`:

```cypher
MATCH (obs:Observation)-[:SUBJECT]->(p:FHIR_Patient)
WHERE
  any(item IN obs.code WHERE item STARTS WITH 'http://loinc.org|788-0') AND
  any(item IN obs.numericValues WHERE item > 12.0)
RETURN p.name[0] AS Patient, obs.text AS Observation, obs.numericValues[0] AS Value
```

Example output:

| Patient | Observation | Value |
|:--------|:------------|------:|
| `Mr. Allen322 Norris589 Ebert178` | `Erythrocyte [DistWidth] in Blood by Automated count` | `14.23` |
| `Mr. Carroll471 Murray856` | `Erythrocyte [DistWidth] in Blood by Automated count` | `13.309` |
| `Mr. Alden634 Frankie174 Hettinger594 JD` | `Erythrocyte [DistWidth] in Blood by Automated count` | `13.694` |
| `Mr. Bruno518 Virgil85 Rippin620` | `Erythrocyte [DistWidth] in Blood by Automated count` | `15.205` |

#### 3. Traverse Encounter to Linked `ImagingStudy` Nodes

Find `ImagingStudy` resources performed in the same `Encounter` as an Erythrocyte Distribution Width (`LOINC 788-0`) observation with a value greater than `15.0`:

```cypher
MATCH (obs:Observation)-[:PERFORMED_IN]->(e:Encounter)<-[:PERFORMED_IN]-(img:ImagingStudy)
WHERE any(item IN obs.code WHERE item STARTS WITH 'http://loinc.org|788-0')
  AND any(item IN obs.numericValues WHERE item > 15.0)
RETURN DISTINCT img.procedureCodeText, img.studyUid, img.seriesUid
```

Example output:

| `img.procedureCodeText` | `img.studyUid` | `img.seriesUid` |
|:------------------------|:---------------|:----------------|
| `Plain X-ray of chest (procedure)` | `1.2.840.99999999.34264206.1037082020670` | `["1.2.840.99999999.1.78527067.1037082020670"]` |
| `Echocardiography (procedure)` | `1.2.840.99999999.64375590.1037082020670` | `["1.2.840.99999999.1.11957029.1037082020670"]` |
