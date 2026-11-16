# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

### Changed

### Deprecated

### Removed

### Fixed

### Security

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