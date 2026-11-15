# Implementation Plan: Move Docker Assets into `docker/` Directory

## Overview & Objective
Relocate the root-level `Dockerfile` and `docker-compose.yml` into a dedicated `docker/`
subfolder and update every path-dependent reference (Compose build definition, `.dockerignore`,
CI workflow, README, and user-facing docs) so Docker builds, Compose orchestration, and CI
verification keep working unchanged when commands are run from the repository root.

## Target Directory Structure
```text
gosmart-graphite/
├── docker/                      # Docker build & orchestration (moved from repo root)
│   ├── Dockerfile               # Multi-stage Docker build (Python 3.14 + uv)
│   └── docker-compose.yml       # Full-stack compose (Graph Server + Neo4j)
├── .dockerignore                # Stays at repo root (applies to the build context)
├── pyproject.toml
├── src/
├── neo4j/
├── docs/
├── tests/
└── README.md
```

## Planned Changes

### 1. Move Files (git history preserved via `git mv`)
- `Dockerfile` -> `docker/Dockerfile`
- `docker-compose.yml` -> `docker/docker-compose.yml`

### 2. Update `docker/docker-compose.yml`
- Add top-level `name: gosmart-graphite` so the Compose project name — and with it network
  names and named-volume prefixes such as `gosmart-graphite_dicom_storage` — remains identical
  to the previous root-directory default after the move.
- `build.context`: `.` -> `..` (Compose resolves the context relative to the Compose file's own
  directory; `..` restores the repository root as the build context).
- `build.dockerfile`: `Dockerfile` -> `docker/Dockerfile` (the `dockerfile` path is resolved
  relative to the build context).

### 3. `docker/Dockerfile` — No Content Changes Required
- Every `COPY` source path (`pyproject.toml`, `uv.lock`, `README.md`, `LICENSE`, `src/`) is
  resolved against the build context, which remains the repository root. Docker `COPY` cannot
  reference files outside the context, so the context must stay at the root and the Dockerfile
  itself needs no edits.

### 4. Update `.dockerignore` (stays at the repository root)
- Replace the stale root entries `docker-compose.yml` and `Dockerfile` with a single `docker/`
  exclusion. The file remains at the root because it applies to the build context directory.

### 5. Update CI (`.github/workflows/ci.yml`)
- Add `file: docker/Dockerfile` to the `docker/build-push-action@v5` step. The action's `file`
  input defaults to `{context}/Dockerfile`, which no longer exists at the root after the move
  (otherwise the Docker Build & Verification job fails).

### 6. Update Documentation (per AGENTS.md rules)
- **`README.md`**: refresh the project structure tree with the `docker/` subtree and update all
  Docker commands and file references:
  - `docker compose -f docker/docker-compose.yml up -d --build` (also `down` / `down -v`)
  - `docker build -t gosmart-graphite -f docker/Dockerfile .`
  - Dockerfile Architecture and environment-variable override section path references.
- **`docs/user_facing/1_getting_started.md`**: point users to `docker/docker-compose.yml` and
  replace the deprecated hyphenated `docker-compose up -d` invocation.

## Verification & Quality Gates
1. `docker compose -f docker/docker-compose.yml config` — validates the resolved build
   context, Dockerfile path, and project name.
2. `docker build -t gosmart-graphite:verify -f docker/Dockerfile .` — full image build
   verification (requires a running Docker daemon).
3. `uv run ruff check .` and `uv run ruff format --check .` — mandatory quality gates.
4. CI `docker-build` job (Docker Buildx, `linux/amd64`) re-verifies the build on push/PR.

---

# Implementation Plan: Update Dependencies to Latest Possible Versions

> The section above documents the completed Docker relocation task and is preserved unchanged.
> The plan below covers the dependency-update task requested on 2026-11-15.

## Overview & Objective
Update every dependency declared in `pyproject.toml` (and the entire transitive graph in
`uv.lock`) to the latest mutually compatible versions available on PyPI for Python >= 3.14,
while preserving:
- The repo's pinning style: packages pinned with `==` are re-pinned at their latest available
  version; packages floored with `>=` keep their floors and float to latest in the lock.
- The security-driven upgrades of transitive dependencies already present in `uv.lock`
  (no locked version may regress — verified via a before/after lock diff).

## Background
- `uv.lock` is currently in sync (`uv lock --check` passes); there is no `[tool.uv]` section
  or `uv.toml`, so prior security workarounds exist purely as raised locked versions of
  transitive dependencies.
- The `==` pins block a plain `uv lock --upgrade`, so they must be re-pinned:
  cryptography, fastapi, fsspec, gcsfs, ipykernel, mcp, neo4j, pydantic, pydantic-settings,
  pydicom, pykka, pytest, python-multipart, rich, typer (runtime) and cyclonedx-bom, ruff (dev).

## Planned Changes
1. Snapshot the current lock (`/tmp/uv.lock.before-update`) for a before/after diff.
2. Discovery pass: temporarily relax all `==` pins to `>=` floors and run
   `uv lock --upgrade --refresh` to compute the latest jointly compatible set.
3. Re-pin: write the discovered latest versions back as `==` pins in `pyproject.toml`.
4. Re-lock (`uv lock`), verify `uv lock --check`, then `uv sync` the environment.

## Verification & Quality Gates (mirroring CI)
1. `uv lock --check` — lockfile synchronization.
2. `uv sync --dev` — environment installs cleanly.
3. `uv run ruff check .` and `uv run ruff format --check .` — mandatory quality gates.
4. `uv run pip-audit` — security audit of the refreshed environment.
5. `uv run cyclonedx-py environment --pyproject pyproject.toml .venv -o sbom.json --validate`
   — SBOM generation/validation, as CI does.
6. `uv run pytest` — full suite where local infrastructure permits; otherwise collect-only
   plus a targeted subset exercising config/models/security/API layers.
7. Before/after lock diff confirming no dependency version decreased.

## Risks & Mitigations
- A jumped major/minor may break code or tests → caught by the gates above; any offending
  pin is held back to the newest version that passes and noted in the change report.
- `pip-audit` may flag advisories in a newest release → prefer the newest version with no
  known advisories; document any unavoidable findings.
