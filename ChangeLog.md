# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> ### PRE-RELEASE PRODUCT - RAPID CHANGES + BREAKING CHANGES
> 
> Please be aware that this product is still in active development with
> rapid development iterations and frequent breaking changes.

## [0.1.1] - 2026-11-23

### Added

- **Semantic Vector Search (`SearchService`)**: Neo4j vector similarity search
  (`SearchResult`, `SearchIncludeLabel`) using Cypher 25 `SEARCH` syntax with
  automatic cached fallback to `db.index.vector.queryNodes` and exact cosine
  fallback when HNSW candidate pools saturate. Supports group-scoped filtering,
  target label/field filters, minimum score thresholds, deletion handling,
  `test_id` isolation, and opt-in context node hydration (`Encounter`,
  `FHIR_Patient`, `ImagingStudy`, `Patient`, `Study`).
- **Decoupled Embedding Models & Background `EmbeddingActor`**: Added
  `Embedding` (with required source `text` persistence and deterministic
  target-scoped IDs) and `EmbedsRelation` (`:EMBEDS`) models, plus Pykka-based
  `EmbeddingActor` with fire-and-forget `notify_embedding` dispatch from
  `FhirActor` after `DiagnosticReport` ingestion.
- **DiagnosticReport Parsing & Embedding (`DiagnosticReportService`)**: Added
  base64 `presentedForm` MIME detection and decoding (text, HTML, XML, RTF,
  PDF), findings/conclusion extraction (preferring structured
  `DiagnosticReport.conclusion`), group and embedding `LLM_Config` resolution
  with `Encounter` and `LostAndFound` fallbacks, concurrent findings/conclusion
  embedding computation, and Neo4j `:EMBEDS` registration.
- **FHIR Configuration Service & `configure` CLI**: Added `FHIRConfiguration`
  models, `HasFhirConfigRelation` (`:HAS_FHIR_CONFIG`), `FHIRConfigService`
  Neo4j persistence and hierarchical group resolution, CLI commands for DICOM
  and FHIR configuration registration, and JSON config templates/scaffolding
  scripts (`devop-scripts/configure-cli.sh`/`.ps1`,
  `devop-scripts/scaffold_configs.sh`/`.ps1`).
- **Group Ownership & Fallback Resolution**: Added `LostAndFound` group
  initialization and fallback helpers, `my_group` upward traversal for nearest
  active group ownership, Patient/Study group association (`:BELONGS_TO` /
  `:PERFORMED_AT`), and idempotent FHIR `Encounter`-to-`Group` `:BELONGS_TO`
  linking.
- **Consolidated Cypher Schema Loader**: Added `db/schema_loader.py` and
  dedicated Cypher schema files (`system_schema.cypher`, `config_schema.cypher`,
  `dicom_schema.cypher`, `embedding_schema.cypher`) with lexical validation,
  exact file/line error reporting, and configurable startup Neo4j connectivity
  retry/backoff settings (`DEV_MODE`, `DB_CONNECT_RETRY_TIMEOUT_SEC`,
  `DB_CONNECT_RETRY_INITIAL_DELAY_SEC`, `DB_CONNECT_RETRY_MAX_DELAY_SEC`).
- **Generic `Result[T]` Envelope & Typed Actor Messages**: Added typed Pydantic
  `Result[T]` response wrapper with standardized `STATUS_*` constants
  (`model/result.py`) and structured actor message models
  (`model/actor_message.py`).
- **MkDocs Documentation Site & Developer Tooling**: Added `docs` dependency
  group (`mkdocs`, `mkdocs-material`), full MkDocs site under `docs/mkdocs/`,
  `pyrefly` static type checker dependency, interactive
  `notebooks/search_playground.ipynb`, startup ASCII logo banner, Windows
  `start.ps1` launch script, Ollama startup scripts
  (`devop-scripts/ollama-start.sh`/`.ps1`), split FHIR mock/plugin startup
  scripts, Neo4j Browser style (`devop-scripts/examples/neo4j_style.grass`), and
  Cypher Query Kit (`devop-scripts/cypher_kit.md`).

### Changed

- **Actor-Based DICOM & FHIR Ingestion Pipeline**: Migrated DICOM and FHIR
  ingestion workflows to the Pykka actor model; renamed `dicom_actor.py` to
  `dicom_ingestion_actor.py`, introduced `DICOM_STAGING_DIR` for temporary
  C-STORE staging prior to ingestion, tracked DICOM association IDs for
  release-triggered ingestion notifications, and integrated `FhirActor` with
  group linking and `EmbeddingActor` notifications.
- **Configuration-to-Group Relationship Architecture**: Overhauled `System`,
  `Group`, `DICOMConfiguration`, `FHIRConfiguration`, and `LLM_Config` graph
  relationships and ownership resolution across services, REST APIs, and CLI.
- **Modularized Model Package Layout**: Extracted shared auditable base models
  into `model/auditable.py`, DICOM configuration models into
  `model/dicom_config.py`, and renamed `model/llm.py` to `model/llm_config.py`
  while keeping public exports aligned in `model/__init__.py`.
- **Branding & Embedding Defaults**: Updated default `DICOM_AE_TITLE` from
  `GOSMART_GS` to `GOSMART_GRAPHITE`, standardized embedding tag references on
  `DEFAULT_EMBEDDING_TAG` (`embedding-v1`), and updated Ollama embedding scripts
  and vector demo notebook defaults to `embeddinggemma-2:270m`.
- **Documentation & Specifications**: Updated `README.md`, design
  specifications (`gsgs_000`, `gsgs_010`, `gsgs_030`, `gsgs_060`), and design
  notes to cover actor-based ingestion, consolidated Cypher schemas, decoupled
  embedding graphs, and Cypher 25 semantic search.

### Deprecated

- N/A.

### Removed

- Removed legacy `src/gosmart_graphite/actor/dicom_actor.py` (replaced by
  `dicom_ingestion_actor.py`) and obsolete `GroupService` helper methods.
- Removed legacy `devop-scripts/configure-llm.sh` (superseded by
  `devop-scripts/configure-cli.sh` and `devop-scripts/configure-cli.ps1`).
- Removed empty placeholder files under `docs/procedures/`.

### Fixed

- **Idempotent FHIR Graph Upserts**: Merged FHIR nodes by `uid` with
  conditional updates only when fields change, and persisted relationships
  against resolved node IDs after merges.
- **FHIR Datetime Parsing, Null Guards & R4B Consistency**: Parsed FHIR
  date/dateTime values into Python `datetime` instances before node mapping,
  added null guards for optional narrative/code/subject/encounter/HTTP client
  fields, and standardized on FHIR R4B `DiagnosticReport` models.
- **Neo4j Async Driver Event-Loop Isolation**: Isolated Neo4j async drivers per
  event loop and simplified startup schema initialization timeout checks.
- **Clean Actor Shutdown**: Added `stop_embedding_actor` to FastAPI lifespan
  teardown alongside DICOM, FHIR, and audit actors.
- **Search Error Handling**: Propagated non-syntax Neo4j errors directly in
  `SearchService` instead of falling back to legacy vector query procedures.
- **Cross-Platform Tests, Imports & Warnings**: Fixed Windows `file://` POSIX
  URI normalization and log file locking issues in unit tests, resolved concrete
  module imports (`llm_config`, `fhir_service`), and cleaned up lint/runtime
  warnings.

### Security

- Rejected path traversal filenames during Cypher schema manifest loading in
  `db/schema_loader.py` and added strict Neo4j vector index identifier
  validation/escaping in `SearchService`.

## [0.1.0] - 2026-11-16

### Added

- Initial release of **Gosmart.Health GRAPHITE** (`gosmart-graphite`): a
  high-performance DICOM Knowledge Graph backend server built on Python 3.14,
  FastAPI, Pydantic, Neo4j, pydicom, pynetdicom, and Pykka.
- FastAPI & Pydantic async REST API with automated OpenAPI schemas (`/docs`),
  strong typing, and validation.
- Neo4j knowledge graph modelling the DICOM hierarchy
  `(:Patient)-[:HAS_STUDY]->(:Study)-[:HAS_SERIES]->(:Series)-[:HAS_INSTANCE]->(:Instance)`
  with uniqueness constraints and indexed lookups.
- Auditable & immutable entity system deriving from the `Auditable` class with
  deterministic UUIDv5 IDs, `test_id` telemetry isolation, UTC Cypher
  timestamps (`CreatedAt`, `ActedAt`, `ExpiresAt`), and immutable action logs.
- Reserved `NOT_` relationship semantics for non-destructive relationship
  soft-deletion within managed Neo4j write transactions.
- DICOM header parsing, file persistence, mock dataset generation, and network
  operations (C-ECHO SCU, C-STORE SCP/SCU, Verification) via pydicom and
  pynetdicom.
- DICOMweb standard RESTful services (STOW-RS, WADO-RS, QIDO-RS).
- Pykka actor-model concurrency (`DicomIngestionActor`, `AuditActor`,
  `StoreSCUActor`) for non-blocking asynchronous ingestion, audit logging, and
  outbound Store SCU transfers.
- Dynamic DICOM SCP lifecycle with runtime configuration, presentation context
  enforcement, service class negotiation, and graceful association draining.
- Authentication & authorization: JWT access/refresh tokens, API keys
  (`gs_live_` prefix), Google OAuth2 / OIDC federated login, scoped RBAC, and
  Argon2 password hashing.
- Model Context Protocol (MCP) server with SSE, StreamableHTTP, and STDIO
  transports for AI agent integration.
- Vector embedding computation service (`EmbeddingService` / `VectorService`)
  powered by Genkit with Ollama and Google Vertex AI / Gemini providers.
- Structured audit trail with configurable retention, Neo4j persistence, and a
  background `AuditActor`.
- Docker & Docker Compose support with a multi-stage Dockerfile, health checks,
  named volumes, and an unprivileged runtime user.
- Typer-based CLI (`gosmart-graphite` / `gosmart-gs`) with `start`, `health`,
  `echo`, `send`, `mcp`, `admin`, and `configure` commands.
- CI/CD pipeline with Ruff linting, pip-audit vulnerability scanning, CycloneDX
  SBOM generation, multi-platform unit tests, and Docker build verification.
- Interactive Jupyter exploration notebook.

### Changed

- N/A (initial release).

### Deprecated

- N/A (initial release).

### Removed

- N/A (initial release).

### Fixed

- N/A (initial release).

### Security

- N/A (initial release).