# Implementation Plan: Dynamic Group-Based LLM Configuration System

> The sections above document completed tasks and are preserved unchanged.
> The plan below covers the dynamic, group-based LLM configuration proposal in
> `docs/design-notes-informal/llm_config.md` (requested on 2026-11-15) and lists
> open questions / missing context requiring user input before implementation.

## Overview & Objective

Replace the single, system-wide `VECTOR_*` / `VERTEX_*` environment-variable LLM
configuration in `src/gosmart_graphite/core/config.py` with a dynamic,
Neo4j-persisted,
purpose-tagged (`tag`), and organization-scoped (`Group`) LLM configuration
system:

- Persist `(:LLM_Config)` nodes (inheriting from `AuditableNode`) linked to
  sponsoring
  `(:Group)` nodes via `:CONFIGURES` relationships.
- Support multiple LLM configurations for different purposes (e.g., vector
  embeddings,
  GraphRAG routing, report parsing, generation) and different sponsoring
  organizations (e.g., separate API keys / billing accounts / endpoints).
- Resolve configurations dynamically at runtime from an active `Group` context
  by traversing
  up the `(:Group)-[:SUB_GROUP_OF*0..]->(:Group)` hierarchy, defaulting to the
  `'GoSmart Health'` root group when no active group context is available.
- Provide a CLI command
  (`configure llm-config --file <llm_config.json> --assign <name or id>`)
  and DevOps wrapper script (`devop-scripts/configure-llm.sh` + template JSON)
  for registering
  and overwriting configurations.

## Current State & Gaps Identified

1. **Configuration (`src/gosmart_graphite/core/config.py`)**: Currently holds a
   single flat
   set of `VECTOR_EMBED_PROVIDER`, `VECTOR_EMBED_MODEL`,
   `VECTOR_EMBED_SERVER_URL`,
   `VECTOR_EMBED_DIMENSIONS`, `VERTEX_AI_API_KEY`, and `VERTEX_AI_LOCATION`
   fields.
2. **Embedding Service (`src/gosmart_graphite/service/embedding_service.py`)**:
    - Reads only from `Settings` (`self.settings`) and caches a single
      `self._ai: Genkit`
      instance per `EmbeddingService` instance (`vector_service` global
      singleton).
    - Uses `VECTOR_EMBED_DIMENSIONS` for both `output_dimensionality`
      (Vertex/GoogleAI) and
      post-hoc truncation in `normalize_vector()`, whereas the proposal
      introduces distinct
      `dimensions` and `dimensions_truncated` fields.
    - Note: `tests/test_vector_service.py` currently imports
      `gosmart_graphite.service.vector_service`
      (which was renamed to `embedding_service.py`).
3. **Graph Models & Schema (`src/gosmart_graphite/model/graph.py`,
   `model/auth.py`, `service/graph_service.py`)**:
    - All graph nodes inherit from `AuditableNode` (subclass of `Auditable`
      providing
      deterministic UUIDv5 `id` via `get_identity_subset()`, `test_id`,
      `Action`, `ActedAt`,
      `ActedBy`, `CreatedAt`, `UpdatedAt`, `ExpiresAt`).
    - `GroupService.get_or_create_catch_all_group()` creates/fetches the default
      group
      `Group(name="GoSmart Health", code="GOSMART_HEALTH", group_type="HEALTH_SYSTEM")`.
    - `DICOMConfigurationNode` uses `(:DICOMConfiguration)-[:SERVES]->(:Group)`
      and
      `(:System)-[:HAS_DICOM_CONFIGURATION]->(:DICOMConfiguration)`.
4. **Files**: An empty `llm_config.md` exists at the repository root alongside
   the populated
   `docs/design-notes-informal/llm_config.md`.

---

## Open Questions & Missing Context (Please Answer Before Implementation)

Per `AGENTS.md` ("Avoid making assumptions if you are not certain, suggest
alternatives and
ask for inputs"), please clarify the following before coding begins:

### 1. `LLM_Config` Deterministic Identity (`id`), Uniqueness, and Overwrite Semantics

The proposal states:

- *"Each node is assignable to one or more `Group` node which is the '
  Sponsoring' organization for the LLM API. The relationship shall be ':
  CONFIGURES'."*
- *"Repeated call to this CLI command will over-write the existing
  configuration."*
- *"traverse up the group tree to find the matching configuration containing the
  `tag`"*

**Questions:**

1. **What defines the unique identity (`get_identity_subset()`) of an
   - The `tag` shall be globally unique across all `LLM_Config` nodes.
2. **Repeated CLI calls with `--assign will use the cloning of the node with 
   the relationshio `NOT_CONFIGURES`** and the new updated value will
   be assigned to the `:CONFIGURES` relationship. No special group specif 
   handling. If multiple groups are assigned to a configuration then we assume 
   that the group cofiguration shall be the truth for all currently associated 
   groups.
3. **Relationship Direction & System Link:**
4. - I changed my mind on this one let's do (:Group) 
     -[:HAS_LLM_CONFIG]->(:Group), and no ohter connection including the one
     from the system is not needed.

### 2. `LLM_Config` Fields, Optionality & Non-Embedding LLM Usage

1. **Optional Fields for Chat/Generation LLMs:** Since `LLM_Config` will serve
   both embeddings and chat/routing LLMs (e.g., `gemini-2.5-flash` for GraphRAG
   routing), should `url`, `dimensions`, and `dimensions_truncated` all be
   optional (`Optional[str] = None` / `Optional[int] = None` or `""` defaults)
   instead of required?
2. **`dimensions` vs. `dimensions_truncated`:** Please confirm the exact runtime
   semantics:
    - `dimensions`: native / requested output dimensionality (e.g., passed as
      `output_dimensionality` to Vertex/Google AI and/or validated against
      returned vector length).
    - `dimensions_truncated`: optional smaller target length for Matryoshka
      truncation + re-normalization in `normalize_vector()`.
3. **Additional Provider Fields:** `EmbeddingService._create_genkit()` currently
   uses `settings.GOOGLE_CLOUD_PROJECT` for
   `VertexAI(project=..., location=..., api_key=...)`. Should `LLM_Config`
   include an optional `project_id: Optional[str]` (and/or
   `extra_config: Optional[Dict[str, Any]]` for LLM parameters like
   `temperature`, `thinking_level`, `timeout`), or should `GOOGLE_CLOUD_PROJECT`
   continue to come from `Settings`/ADC?
4. **Canonical `tag` Names:** What default `tag` string should
   `EmbeddingService` look up when no explicit tag is passed (e.g.,
   `"embedding"` or `"vector_embedding"`)? Are there other initial canonical
   tags we should document/reserve now (e.g., `"router"`, `"rag"`,
   `"report_parser"`)?

### 3. Security & Storage of `api_key`

The proposal specifies `api_key: str` (default `""`). Storing plaintext API keys
on Neo4j nodes makes them readable via Cypher queries (`gosmart-graphite query`
or MCP) and database backups.

- ** for the API key Support either literal
  strings or env-var references (e.g., `${GOSMART_GS_VERTEX_AI_API_KEY}`) stored
  in Neo4j, or store plaintext under the existing hardened-platform security
  assumption. In production the key will come from the Secret Manager with
  encryption at rest.


### 4. CLI Design & `--assign` Resolution Behavior

1. **CLI Command Entrypoint:** The project's primary CLI is Typer-based
   (`src/gosmart_graphite/cli.py`, registered as `gosmart-graphite` and
   `gosmart-gs`). Should `configure llm-config` be:
    - A Typer command group
      (`gosmart-graphite configure llm-config --file ... --assign ...`)
      implemented in `src/gosmart_graphite/cli/configure.py` and attached to
      `cli_app` (and optionally runnable directly via
      `python -m gosmart_graphite.cli.configure llm-config ...`).
2. **JSON File Format:** --file <llm_config.json>` accept only a signle JSON 
   instance and not an array.
3. **`--assign <name or id>` Matching & Missing Default Group:**
    - Should `--assign` match active non-deleted `Group` nodes by
      `g.id = $val OR g.name = $val` (and should `g.code = $val` also be
      matched)?
    - Matching `0` groups is also a runtime error (in addition to
      `> 1` matching groups).
    - When `--assign` is omitted, if `'GoSmart Health'` does not yet exist in
      Neo4j, that is a complete failure.

### 5. Runtime Resolution, Fallback to `.env`, and Client Caching

1. **Legacy `VECTOR_*` / `VERTEX_*` Env Vars:**
    - This product has not yet been released.
    - Remove `VECTOR_*` / `VERTEX_*` from
      `Settings` and require Neo4j `LLM_Config` for all runtime calls.
2. **Upward Group Tree Traversal & Fallback:**
    - Starting from active `group_id`, traverse
      `(active:Group)-[:SUB_GROUP_OF*0..]->(ancestor:Group)` ordered by hop
      distance (`length(path) ASC`), skipping deleted/inactive `Group` nodes,
      `NOT_CONFIGURES` / deleted `LLM_Config` nodes, and matching `tag`.
    - If the active group's `SUB_GROUP_OF` chain does not reach
      `'GoSmart Health'`, should resolution fall back to `'GoSmart Health'`'s
      `LLM_Config` for that `tag` before falling back to `.env`?
    - If a group has multiple `LLM_Config`s with the same `tag` at the same
      hierarchy depth, should that raise a runtime error or pick the most
      recently updated?
3. **Active Group Context Propagation:**
    - REST endpoints already resolve `get_current_group_context` (`X-Group-ID`
      or user's primary group). Should `EmbeddingService` methods (`embed_text`,
      `embed_text_async`) accept optional `group_id: Optional[str] = None` and
      `tag: str = "embedding"` parameters (plus optional `ContextVar` support
      for MCP/request-scoped calls)?

### 6. Scope Boundaries & Existing Cleanup

1. **REST API & MCP Tools:** Deliverable also should include FastAPI REST
   endpoints (`/api/v1/configurations/llm`) and MCP tools (mirroring
   `DICOMConfiguration` in `api/v1/endpoints/system.py` and
   `service/mcp_service.py`), or defer REST/MCP to a subsequent issue?
2. **`devop-scripts`:**
    - Confirmed:`devop-scripts/configure-llm.sh` should invoke
      `uv run gosmart-graphite configure llm-config ...` with a sample/template
      file (e.g., `devop-scripts/llm_config.template.json`).
    - Confirmed that adding `"LLM_Config"` to `PRESERVED_CONFIGURATION_LABELS` 
      in
      `devop-scripts/cleanup-data.py` so data cleanup preserves LLM
      configurations.
3. **Housekeeping:** Confirm whether to:
    - Please Fix the broken import in `tests/test_vector_service.py`
      (`gosmart_graphite.service.vector_service` → `embedding_service`).
    - Please Remove the empty 0-byte `llm_config.md` at the repo root (keeping
      `docs/design-notes-informal/llm_config.md`).

---

## Proposed Architecture & Planned Changes (Pending Answers Above)

### 1. Graph Models (`src/gosmart_graphite/model/llm.py` & `model/audit.py`)

- **`LLM_Config` (or `LLMConfigNode(AuditableNode)`)** with
  `__label__ = "LLM_Config"` and fields:
    - `tag: str`
    - `provider: str` (normalized lowercase, e.g. `ollama`, `vertexai`,
      `googleai`)
    - `model: str`
    - `url: str = ""` (or `Optional[str]`)
    - `dimensions: Optional[int] = None`
    - `dimensions_truncated: Optional[int] = None`
    - `api_key: str = ""` (encrypted/masked per Q3 decision)
    - `location: str = "us-central1"`
    - Optional `project_id` / `extra_config` per Q2.3.
- **`ConfiguresRelation(AuditableRelation)`** with `__type__ = "CONFIGURES"`
  (`LLM_Config -> Group`).
- Add `AuditAction` entries in `src/gosmart_graphite/model/audit.py` (e.g.,
  `LLM_CONFIGURATION_CREATED`, `LLM_CONFIGURATION_CHANGED`,
  `LLM_CONFIGURATION_ASSIGNED`, `LLM_CONFIGURATION_REMOVED`).
- Register uniqueness constraints / indexes for `LLM_Config` (`id`, `tag`) in
  `GraphService.init_schema_constraints()`.

### 2. LLM Configuration Service (`src/gosmart_graphite/service/llm_config_service.py`)

- **`LLMConfigService`** providing both `async` and `_sync` Neo4j operations (so
  both FastAPI/async code and Pykka actors / sync notebooks work without
  event-loop conflicts):
    - `resolve_group(identifier: Optional[str])`: finds active `Group` by `id` /
      `name` (or default `'GoSmart Health'`), raising an error if `> 1` (or `0`)
      matches are found.
    - `upsert_llm_config(config, assign_group=..., acted_by=..., test_id=...)`:
      idempotent `MERGE` with deterministic `id`, updating properties on
      overwrite and linking `(:LLM_Config)-[:CONFIGURES]->(:Group)`.
    - `resolve_config_for_group(tag: str, group_id: Optional[str] = None)` (+
      `_sync`): traverses upward `(:Group)-[:SUB_GROUP_OF*0..]->(:Group)`
      ordered by path length to find the nearest matching active `LLM_Config`
      for `tag`, falling back to `'GoSmart Health'` (and optionally `.env` per
      Q5.1).
    - CRUD + assignment helpers (`list_llm_configs`, `get_llm_config`,
      `assign_to_group`, `dissociate_from_group` using `NOT_CONFIGURES`).

### 3. Dynamic Client Factory & `EmbeddingService` Refactor (`src/gosmart_graphite/service/embedding_service.py`)

- Replace single `self._ai` / `self.settings` coupling with dynamic resolution
  via `LLMConfigService` (defaulting to `tag="embedding"`, optional `group_id` /
  active group context, and thread-safe Genkit instance caching keyed by config
  identity + fingerprint so overwrites reload cleanly).
- Apply `dimensions` (provider `output_dimensionality` / expected length) and
  `dimensions_truncated` (Matryoshka truncation in `normalize_vector()`).
- Preserve direct `Settings` injection support for unit testing or fallback mode
  (per Q5.1).

### 4. CLI (`src/gosmart_graphite/cli/configure.py` & `src/gosmart_graphite/cli.py`)

- Add `configure` subcommand group with `llm-config`:
    -
    `gosmart-graphite configure llm-config --file <llm_config.json> [--assign <name or id>]`
    - Validate JSON against `LLM_Config` Pydantic schema, resolve target group
      (default `'GoSmart Health'`), error clearly if `--assign` matches `0` or
      `> 1` groups, upsert & assign, and print a Rich summary table (masking
      `api_key`).

### 5. DevOps Scripts (`devop-scripts/`)

- `devop-scripts/configure-llm.sh`: executable shell wrapper invoking
  `uv run gosmart-graphite configure llm-config` with `--file` and optional
  `--assign`.
- `devop-scripts/llm_config.template.json`: sanitized template JSON with
  placeholder values (no real secrets).
- `devop-scripts/cleanup-data.py`: add `"LLM_Config"` to
  `PRESERVED_CONFIGURATION_LABELS` so test data cleanup preserves LLM
  configurations.

### 6. Tests & Documentation (per `AGENTS.md`)

- Modular tests using `test_id` isolation (pre-test cleanup, no post-test
  deletion) covering:
    - `LLM_Config` deterministic ID, validation, and `NOT_CONFIGURES`
      soft-dissociation.
    - CLI `configure llm-config` (single/multi-config, overwrite, default
      `'GoSmart Health'`, `--assign` 0 / 1 / >1 match error cases).
    - Upward group-tree resolution (`Child Group -> Parent Group -> Root Group`
      override & inheritance) and `EmbeddingService` dynamic provider
      switching + `dimensions_truncated`.
- Update `README.md`, `docs/design/gsgs_000_software_requirements_spec.md`,
  `docs/design/gsgs_010_system_design_specification.md`,
  `docs/design/gsgs_030_verification_and_validation_plan.md`,
  `docs/design/gsgs_060_authentication_authorization.md`,
  `docs/design-notes-informal/vector_embedding.md`, and
  `devop-scripts/README.md`.

## Verification & Quality Gates

1. `uv run ruff check .` and `uv run ruff format --check .` — mandatory lint and
   format checks.
2.
`uv run pytest tests/test_llm_config.py tests/test_vector_service.py tests/test_config.py` —
targeted unit and Neo4j integration tests.
3. CLI smoke test via `devop-scripts/configure-llm.sh` against a local Neo4j
   instance.
