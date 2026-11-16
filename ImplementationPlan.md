# Implementation Plan: Auto-Generate `LocationNode`, `OrganizationNode`, and `PractitionerNode` from FHIR References

## Summary

Currently, [`LocationNode`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/model/fhir/nodes.py#L295-L351), [`OrganizationNode`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/model/fhir/nodes.py#L417-L457), and [`PractitionerNode`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/model/fhir/nodes.py#L555-L611) are defined in the graph model and Neo4j schema, along with their relationship classes in [`relations.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/model/fhir/relations.py), but they are never fetched, synthesized, or upserted during FHIR ingestion.

This enhancement updates [`FHIRBatchFetcher`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/service/fhir_service.py#L599-L748), [`FhirService`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/service/fhir_service.py#L751-L1485), [`FhirActor`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/actor/fhir_actor.py#L12-L176), and [`relations.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/model/fhir/relations.py) so that whenever ingested resources contain references to `Location/`, `Practitioner/`, `Organization/`, or `serviceProvider`, the corresponding nodes and intra-FHIR relationships are automatically fetched (with fallback to reference-derived stub nodes) and upserted into Neo4j.

---

## Requirements & Design Decisions

1. **Fetch-First with Reference-Stub Fallback**:
   - During encounter graph retrieval (`fetch_encounter_graph` / `fetch_enc_relevant_resources`), concurrently fetch referenced `Location`, `Organization`, and `Practitioner` resources from the FHIR server.
   - If a referenced resource cannot be fetched (e.g., 404, server error, or offline conversion via `convert_relevant_resources` / `upsert_relevant_resources`), automatically synthesize a stub `LocationNode`, `OrganizationNode`, or `PractitionerNode` from the reference element.
   - If the reference element includes `display` text (`"reference|display"`), populate the node's `name` field (`str` for `LocationNode` and `OrganizationNode`; `[display]` `List[str]` for `PractitionerNode`). If a full resource was fetched without a `name`, backfill `name` from the reference `display` text.

2. **Non-Clobbering Upsert Semantics for Stub Nodes**:
   - `FHIRDomainResourceNode` requires `resource` or `resourceUri` to be non-null. Synthesized stub nodes will store a minimal valid FHIR JSON payload (e.g., `{"resourceType": "Location", "id": "<id>", "name": "<display>"}`) in `resource`.
   - When upserting a reference-derived stub node into Neo4j (`MERGE ... ON MATCH SET`), existing richer properties (such as full `resource` JSON, `identifiers`, `address`, `telecom`, etc.) from a previously ingested full resource must **not** be overwritten; only missing fields (like `name` when previously null) and audit metadata (`Action`, `ActedAt`, `ActedBy`, `UpdatedAt`) are updated. Conversely, upserting a full resource over an existing stub upgrades all properties.

3. **Targeted Reference Fields** (modeled on existing FHIR node classes):
   - **`Location` references**:
     - `Encounter.location` (`EncounterNode.location`)
     - `ImagingStudy.location` (`ImagingStudyNode.location`)
     - `Procedure.location` (`ProcedureNode.location`)
   - **`Organization` references**:
     - `Encounter.serviceProvider` (`EncounterNode.serviceProvider`, defaulting bare IDs to `Organization`)
     - `Patient.managingOrganization` (`FHIRPatientNode.managingOrganization`, defaulting bare IDs to `Organization`)
     - Polymorphic fields when targeting `Organization/...`: `ServiceRequestNode.requester`, `DiagnosticReportNode.performer`, `ObservationNode.performer`, `ProcedureNode.performer`, `ProcedureNode.recorder`, `ProcedureNode.asserter`
     - Fetched `LocationNode.managingOrganization`, `OrganizationNode.partOf`, `PractitionerNode.organization`
   - **`Practitioner` references**:
     - `Encounter.participantIndividual` (`EncounterNode.participantIndividual`)
     - `ServiceRequest.requester` (`ServiceRequestNode.requester`)
     - `DiagnosticReport.performer` (`DiagnosticReportNode.performer`)
     - `Observation.performer` (`ObservationNode.performer`)
     - `Procedure.performer`, `Procedure.recorder`, `Procedure.asserter` (`ProcedureNode.performer`, `recorder`, `asserter`)

4. **Intra-FHIR Relationships Created**:
   - `(:Encounter)-[:PERFORMED_AT]->(:Location)` (via `Encounter.location`)
   - `(:ImagingStudy)-[:PERFORMED_AT]->(:Location)` (via `ImagingStudy.location`)
   - `(:Procedure)-[:PERFORMED_AT]->(:Location)` (via `Procedure.location`)
   - `(:Encounter)-[:SERVICE_PROVIDER]->(:Organization)` (new `ServiceProviderRelation` via `Encounter.serviceProvider`)
   - `(:FHIR_Patient)-[:PART_OF]->(:Organization)` or `(:FHIR_Patient)-[:SERVICE_PROVIDER]->(:Organization)` — *note: proposed `PART_OF` for `Location.managingOrganization`/`Location.partOf`/`Organization.partOf` and `MANAGED_BY` or `SERVICE_PROVIDER` or `PART_OF` for `Patient.managingOrganization`; proposed below to use `ServiceProviderRelation` (`SERVICE_PROVIDER`) for `Encounter.serviceProvider` and `PartOfRelation` (`PART_OF`) for `Location`/`Organization` hierarchy + `Patient.managingOrganization` unless a separate `MANAGED_BY` relation is preferred*
   - `(:Practitioner|Organization)-[:REQUESTS]->(:ServiceRequest)` (via `ServiceRequest.requester`)
   - `(:Practitioner|Organization)-[:PERFORMS]->(:Procedure)` (via `Procedure.performer`)
   - `(:Practitioner|Organization)-[:PERFORMS]->(:Observation)` (via `Observation.performer`)
   - `(:Practitioner|Organization)-[:PERFORMS]->(:Encounter)` (via `Encounter.participantIndividual`)
   - `(:Practitioner|Organization)-[:CREATES]->(:DiagnosticReport)` (via `DiagnosticReport.performer`)
   - `(:Location)-[:PART_OF]->(:Organization|Location)` (via `Location.managingOrganization` and `Location.partOf`)
   - `(:Organization)-[:PART_OF]->(:Organization)` (via `Organization.partOf`)
   - `(:Practitioner)-[:BELONGS_TO]->(:Organization)` (via `Practitioner.organization`)

---

## Proposed File Changes

### 1. [`src/gosmart_graphite/model/fhir/relations.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/model/fhir/relations.py) & Exports
- Add `ServiceProviderRelation(AuditableRelation)` with `__type__: ClassVar[str] = "SERVICE_PROVIDER"` connecting `Encounter -> Organization`.
- Append `ServiceProviderRelation` to `FHIR_RELATION_CLASSES`.
- Export `ServiceProviderRelation` from [`src/gosmart_graphite/model/fhir/__init__.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/model/fhir/__init__.py) and [`src/gosmart_graphite/model/__init__.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/model/__init__.py).

### 2. [`src/gosmart_graphite/service/fhir_service.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/service/fhir_service.py)
- **Models (`EncounterContext`, `EncRelevantResources`)**:
  - Import `Location`, `Organization`, `Practitioner` from `fhir.resources.R4B.*`.
  - Import `LocationNode`, `OrganizationNode`, `PractitionerNode`, `FhirPerformedAtRelation`, `RequestsRelation`, `PerformsRelation`, `CreatesRelation`, `PartOfRelation`, `FhirBelongsToRelation`, `ServiceProviderRelation` from `gosmart_graphite.model.fhir`.
  - Add `locations`, `organizations`, and `practitioners` fields (with `default_factory=list`) to `EncounterContext` and `EncRelevantResources`.
- **Reference & Formatting Helpers**:
  - Add `_parse_reference_macro(ref_macro, default_type=None) -> Optional[tuple[str, str, Optional[str]]]` returning `(resource_type, resource_id, display)`.
  - Add `_format_telecom` and `_format_address` / `_format_addresses` helpers for `ContactPoint` and `Address` FHIR elements.
  - Add full resource converters:
    - `location_to_node(location, test_id=None, acted_by="system") -> LocationNode`
    - `organization_to_node(organization, test_id=None, acted_by="system") -> OrganizationNode`
    - `practitioner_to_node(practitioner, test_id=None, acted_by="system") -> PractitionerNode`
  - Add reference-stub builders:
    - `reference_to_location_node(resource_id, display=None, test_id=None, acted_by="system") -> LocationNode`
    - `reference_to_organization_node(resource_id, display=None, test_id=None, acted_by="system") -> OrganizationNode`
    - `reference_to_practitioner_node(resource_id, display=None, test_id=None, acted_by="system") -> PractitionerNode`
- **Batch Fetching (`FHIRBatchFetcher.fetch_encounter_graph` & `FhirService.fetch_enc_relevant_resources`)**:
  - After fetching the primary encounter resources, extract all unique `Location/`, `Organization/`, and `Practitioner/` references from the encounter and related resources.
  - Concurrently fetch each referenced resource via `fetch_by_reference` (`return_exceptions=True`), populating `EncounterContext.locations`, `organizations`, `practitioners` (and recording any HTTP errors in `errors` without failing the rest of the batch).
  - Validate fetched dicts into `Location`, `Organization`, and `Practitioner` models in `fetch_enc_relevant_resources`.
- **Graph Conversion & Upsert (`convert_relevant_resources`, `upsert_relevant_resources`)**:
  - Convert all fetched `Location`, `Organization`, and `Practitioner` resources into their respective node models.
  - Scan all modeled reference fields across the converted nodes; for any `Location/`, `Practitioner/`, or `Organization/` reference (and `serviceProvider` / `managingOrganization` with default type `"Organization"`):
    - If already present in the fetched map, backfill `name` from reference `display` only if `name` is currently `None` or empty.
    - Otherwise, create a stub node with `resource_id`, `name` populated from `display` (when present), and minimal JSON `resource`, tracking stub node IDs so `upsert_relevant_resources` uses non-clobbering `ON MATCH SET` Cypher semantics (preserving existing full-resource properties and only setting `n.name = coalesce(n.name, $props.name)` when matching an existing node).
  - Create all corresponding intra-FHIR `AuditableRelation` edges listed above, deduplicated by deterministic `rel.id`.

### 3. [`src/gosmart_graphite/actor/fhir_actor.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/src/gosmart_graphite/actor/fhir_actor.py)
- Update `_has_resources` to also check `resources.locations`, `resources.organizations`, and `resources.practitioners`.
- Include `locations`, `organizations`, and `practitioners` counts in the `Got the FHIR resources for Study` structured log event.

### 4. Documentation ([`docs/design-notes-informal/fhir_schema.md`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/docs/design-notes-informal/fhir_schema.md))
- Document `:SERVICE_PROVIDER` (`Encounter -> Organization`) and the additional `:PERFORMED_AT` / `:PERFORMS` / `:REQUESTS` / `:CREATES` / `:PART_OF` edges wired from ingested FHIR references.

### 5. Tests ([`tests/unit/test_fhir_models.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/tests/unit/test_fhir_models.py) & [`tests/unit/test_fhir_service.py`](file:///Users/manabutokunaga/development/dicom-fhir/gosmart-graphite/tests/unit/test_fhir_service.py))
- Update `test_fhir_relations_and_negation` in `tests/unit/test_fhir_models.py` for the updated `FHIR_RELATION_CLASSES` count (19) and test `ServiceProviderRelation`.
- Add unit tests in `tests/unit/test_fhir_service.py`:
  - Full resource conversion tests for `location_to_node`, `organization_to_node`, and `practitioner_to_node`.
  - Stub node generation from references (`Location/...`, `Practitioner/...`, `Organization/...`, `Encounter.serviceProvider`) both with and without `display` text, verifying `name` population and intra-FHIR edge creation.
  - Mocked `FHIRBatchFetcher.fetch_encounter_graph` and `FhirService.fetch_enc_relevant_resources` tests verifying concurrent fetching of referenced `Location`, `Organization`, and `Practitioner` resources, plus graceful fallback to stub nodes when a reference returns 404/500.
  - Neo4j integration test (when Neo4j is reachable) verifying stub-to-full upgrade and full-to-stub non-clobbering upsert idempotency (`test_id`-scoped cleanup before test).

---

## Verification Plan

1. Run unit & integration tests:
   ```bash
   uv run pytest tests/unit/test_fhir_models.py tests/unit/test_fhir_service.py
   uv run pytest
   ```
2. Run linting and formatting checks:
   ```bash
   uv run ruff check .
   uv run ruff format --check .
   ```
