# Implementation Plan: Make `tests/unit` Fully Offline — Fix CI Failures Caused by External Neo4j Connections

## Summary

The CI workflow (`.github/workflows/ci.yml`) correctly runs only `pytest tests/unit`, but 6 unit
tests fail on GitHub Actions because they silently connect to an **external Neo4j database**
(`bolt://localhost:7687` by default on CI, where nothing is listening):

- `tests/unit/test_system_api.py` — all 4 tests (`test_api_system_bootstrap_and_get`,
  `test_api_system_update`, `test_api_dicom_configuration_crud`, `test_api_system_test_cleanup`)
  fail with `neo4j.exceptions.ServiceUnavailable: Couldn't connect to localhost:7687`.
- `tests/unit/test_mcp_security.py` — 2 tests (`test_mcp_streamable_http_auth_and_handshake`,
  `test_mcp_streamable_http_via_main_app_lifespan`) fail with `AssertionError: assert 401 == 200`.

The same tests **pass locally** only because the developer's root `.env` points
`GOSMART_GS_DB_CONNECTION_URI` at a reachable remote Neo4j Aura instance, which masks the
unintended live-database dependency. Reproduced locally by overriding
`GOSMART_GS_DB_CONNECTION_URI=bolt://localhost:7687` (dead port) — exactly 6 failed, 121 passed.

## Root Causes

1. **`tests/unit/test_system_api.py`**: The system router registers router-level dependencies
   `dependencies=[Depends(get_current_user), Depends(get_current_group_context)]`
   (`src/gosmart_graphite/api/v1/endpoints/system.py`). The test `client` fixture overrides only
   `get_current_user`; `get_current_group_context` runs unmocked and calls
   `group_service.get_user_affiliations()` → real Neo4j query → `ServiceUnavailable` → HTTP 500
   instead of the asserted 200.

2. **`tests/unit/test_mcp_security.py`**: `MCPAuthMiddleware.__call__`
   (`src/gosmart_graphite/service/mcp_service.py`) calls `user_service.get_user_by_id()` for any
   valid JWT (and `user_service.authenticate_api_key()` for the `X-API-Key` path — also used by
   the passing SSE tests). On CI the connection error is swallowed by the middleware's broad
   `except`, downgrading valid tokens to `401 Unauthorized`, so the `assert 200` fails.

3. **`tests/unit/test_mcp.py`** (hidden via socket-audit): `test_mcp_generate_mock_tool` invokes
   the real `generate_mock_dicom` tool, whose `graph_service.save_hierarchy()` call writes the
   mock hierarchy into the live Neo4j database (also swallowed by a broad `except`). On CI this
   passes only because `localhost:7687` refuses instantly; against a reachable DB it performs
   real writes and adds ~18s of TLS round-trips.

## Changes (test-only; no production code changes)

1. **`tests/unit/test_system_api.py`**
   - Import `get_current_group_context` and add
     `app.dependency_overrides[get_current_group_context] = lambda: None` in the `client` fixture
     (mirrors the "no primary affiliation" resolution without any DB access).

2. **`tests/unit/test_mcp_security.py`**
   - Add an `offline_mcp_auth` fixture (monkeypatch) that replaces
     `user_service.get_user_by_id` and `user_service.authenticate_api_key` with
     `AsyncMock(return_value=None)`, forcing the middleware's deterministic JWT-claims fallback
     path with zero network I/O.
   - Apply the fixture to all 4 tests in the module so no MCP auth request can reach the database.

3. **`tests/unit/test_mcp.py`**
   - In `test_mcp_generate_mock_tool`, patch `graph_service.save_hierarchy` with an
     `AsyncMock` and assert it was awaited, verifying the tool wiring without any database access.

## Out of Scope

- DB-backed behaviour (real user lookup, RBAC propagation) remains covered by
  `tests/integration/` and `tests/live/` suites, which CI intentionally does not run.
- No CI workflow changes required; no user-facing usage, configuration, or CLI changes.

## Verification Plan

1. Simulate CI (unreachable Neo4j) — all unit tests must pass offline:
   ```bash
   GOSMART_GS_DB_CONNECTION_URI=bolt://localhost:7687 \
   GOSMART_GS_DICOM_SCP_ENABLED=false \
   GOSMART_GS_STORAGE_ROOT_URI=file:///tmp/gosmart-graphite-ci-storage \
   GOSMART_GS_LOG_TO_FILE=false \
   uv run pytest tests/unit --verbose
   ```
2. Standard run with local `.env` (previously-masking scenario): `uv run pytest tests/unit` must stay green.
3. Quality gates:
   ```bash
   uv run ruff check .
   uv run ruff format --check .
   ```
