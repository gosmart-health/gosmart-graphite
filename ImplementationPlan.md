# Implementation Plan: Project Directory Restructuring (`src/` Layout)

## Overview & Objective
Restructure the Python source packages and application entrypoints into a top-level `src/` directory while preserving non-application resources (`neo4j/`, `devop-scripts/`, `notebooks/`, `docs/`, `skills/`, and `tests/`) at the repository root.

## Target Directory Structure
```text
gosmart-dicom-py-graph-server/
├── src/
│   ├── actor/
│   ├── api/
│   ├── cli/
│   │   ├── __init__.py
│   │   └── admin.py
│   ├── core/
│   ├── db/
│   │   ├── __init__.py
│   │   ├── fhir_schema.cypher
│   │   └── neo4j.py
│   ├── model/
│   ├── service/
│   ├── cli.py
│   └── main.py
├── devop-scripts/
├── docs/
├── neo4j/
├── notebooks/
├── skills/
├── tests/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── start.sh
└── README.md
```

## Planned Changes

### 1. Move Python Application Code into `src/`
- Move `actor/` -> `src/actor/`
- Move `api/` -> `src/api/`
- Move `cli/` -> `src/cli/`
- Move `core/` -> `src/core/`
- Move `db/` -> `src/db/`
- Move `model/` -> `src/model/`
- Move `service/` -> `src/service/`
- Move `cli.py` -> `src/cli.py`
- Move `main.py` -> `src/main.py`

### 2. Update Build & Packaging (`pyproject.toml`)
- Configure `[tool.pytest.ini_options]` with `pythonpath = ["src", "."]` so tests and root utilities resolve both `src/` packages (`actor`, `api`, `cli`, `core`, `db`, `model`, `service`, `main`) and `tests.*` cleanly.
- Configure `[tool.hatch.build.targets.wheel]` to package from `src/`:
  ```toml
  [tool.hatch.build.targets.wheel]
  packages = [
      "src/actor",
      "src/api",
      "src/cli",
      "src/core",
      "src/db",
      "src/model",
      "src/service",
  ]
  only-include = [
      "src/actor",
      "src/api",
      "src/cli",
      "src/core",
      "src/db",
      "src/model",
      "src/service",
      "src/cli.py",
      "src/main.py",
  ]

  [tool.hatch.build.targets.wheel.sources]
  "src" = ""
  ```

### 3. Update Runtime Scripts & Container Configurations
- **`start.sh`**: Update to `PYTHONPATH=src uv run python src/main.py` (or `uv run python src/main.py` after `uv sync` installs the editable root package).
- **`Dockerfile`**:
  - Copy `src/` into `/app/src/` instead of individual top-level package directories.
  - Set `PYTHONPATH="/app/src"` in runtime environment variables.
  - Ensure `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8086"]` works seamlessly.
- **`src/main.py` & `src/cli.py`**: Ensure `sys.path` includes `Path(__file__).resolve().parent` when invoked directly as `python src/main.py` or `python src/cli.py`.

### 4. Update Top-Level Scripts, Notebooks, and Tests
- **`devop-scripts/cleanup-data.py`**: Add `PROJECT_ROOT / "src"` to `sys.path` so standalone execution (`python devop-scripts/cleanup-data.py`) resolves `core`, `db`, and `service`.
- **`notebooks/*.ipynb`**: Update `sys.path` setup in `exploration.ipynb`, `validator.ipynb`, and `vector_emdedding_demo.ipynb` to include `PROJECT_ROOT / "src"`.
- **`tests/test_fhir_models.py`**: Update `cypher_file` path reference from `PROJECT_ROOT / "db" / "fhir_schema.cypher"` to `PROJECT_ROOT / "src" / "db" / "fhir_schema.cypher"`.

### 5. Update Documentation (`README.md` & `docs/`)
- Update project directory tree and CLI / server execution examples in `README.md` and `docs/`.

### 6. Verification & Quality Gates
- Re-sync environment (`uv sync --dev`) so editable package metadata points to `src/`.
- Run `uv run ruff check .` and `uv run ruff format --check .`.
- Run `uv run pytest` to verify all unit and integration tests pass.
- Run PyCharm `lint_files` inspection on modified files.
