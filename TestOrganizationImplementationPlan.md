# Implementation Plan: Separate Unit, Integration, and Live Tests

## Summary

Organize the existing pytest suite into `tests/unit/`, `tests/integration/`, and `tests/live/` without changing test behavior or test-data retention policies. CI will run only self-contained unit tests, while integration and live suites remain available for explicit local execution.

## Changes

1. Move self-contained test modules into `tests/unit/` and infrastructure-dependent modules into `tests/integration/`.
2. Split mixed modules by test function, retaining all existing test names, assertions, mocks, fixtures, and `test_id` behavior.
3. Move tests requiring external MDICOM, FHIR, MCP, or Gemini services into `tests/live/`.
4. Keep shared assets, configuration, and fixtures under `tests/`; correct paths affected by directory depth.
5. Prevent unit API fixtures from running production application lifespan side effects. Integration tests retain real lifespan behavior.
6. Update CI to execute `uv run pytest tests/unit --verbose` without injecting live-service secrets.
7. Update README, verification references, and the existing FHIR implementation plan for new test paths.
8. Add explicit Ruff exclusions for generated notebook/virtual-environment content only if required by existing repository contents; do not weaken lint rules for source or tests.

## Verification

- Collect every test and compare test names/counts with the pre-move suite.
- Run `uv run pytest tests/unit --verbose` without live credentials.
- Run `uv lock --check`, `uv run ruff check .`, and `uv run ruff format --check .`.
- Run `uv run pip-audit` and generate/validate the CycloneDX SBOM.
- Build `docker/Dockerfile` for `linux/amd64` when the local Docker service is available.
- Confirm no integration or live test is collected by the CI unit-test command.
