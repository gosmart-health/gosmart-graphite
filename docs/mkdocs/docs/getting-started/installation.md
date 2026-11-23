# Installation

This guide runs GRAPHITE locally with `uv` against an existing Neo4j instance.

## 1. Install dependencies

Python 3.14 and the virtual environment are managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

## 2. Configure the environment

Copy the environment template and adjust values for your machine:

```bash
cp .env.example .env
```

At minimum, point GRAPHITE at your Neo4j and (optionally) set the bootstrap
admin credentials:

```bash
GOSMART_GS_DB_CONNECTION_URI=bolt://localhost:7687
GOSMART_GS_DB_USER=neo4j
GOSMART_GS_DB_PASSWORD=your-password
GOSMART_GS_INITIAL_ADMIN_EMAIL=admin@gosmart.health
GOSMART_GS_INITIAL_ADMIN_PASSWORD=MySecurePassword123!
GOSMART_GS_INITIAL_ADMIN_USERNAME=admin
```

The full list of settings lives in [Environment Variables](../configuration/environment-variables.md).

## 3. (Optional) Scaffold starter configurations

Register starter DICOM, FHIR, and LLM configurations in Neo4j before starting
the server:

```bash
./devop-scripts/scaffold_configs.sh
```

This uses the sanitized templates from `devop-scripts/` — see
[DICOM](../configuration/dicom-configurations.md),
[FHIR](../configuration/fhir-configuration.md), and
[LLM](../configuration/llm-configurations.md) configuration pages for the
exact fields.

## 4. Start the server

Ensure `.env` is configured and Neo4j is running, then launch:

- Linux/macOS:

  ```bash
  ./start.sh
  ```

- Windows (PowerShell):

  ```powershell
  .\start.ps1
  ```

On startup the server:

1. Loads settings from `GOSMART_GS_*` environment variables / `.env`,
2. Ensures the Cypher schema (constraints and indexes) is in place,
3. Bootstraps the immutable root `(:System)` node with a promiscuous
   (`ae_title = "*"`) DICOM entry and the default `'GoSmart Health'` and
   `'Lost and Found'` groups,
4. Creates the initial Platform Administrator **if none exists** and
   `GOSMART_GS_INITIAL_ADMIN_PASSWORD` is set (idempotent on restarts),
5. Starts the Pykka actors and (if configured) the DICOM SCP and FHIR poller.

Interactive API documentation is then available at:

- **Swagger UI**: [http://localhost:8086/docs](http://localhost:8086/docs)
- **ReDoc**: [http://localhost:8086/redoc](http://localhost:8086/redoc)

## 5. (Optional) Reset data between test cycles

To wipe the entire database and reset the system to defaults, run the following
Cypher query in [Neo4j Browser](http://localhost:7474):

```cypher
MATCH (n)
DETACH DELETE n;
```

To clear only ingested DICOM/FHIR data while preserving configurations and
users, run the cleanup utility:

```bash
uv run devop-scripts/cleanup-data.py
```

> [!NOTE]
> Test-created data is retained after a run and only cleared before the next
> test, so you can inspect post-run state manually.

## Next steps

- Verify the install and ingest your first study: [First Steps](first-steps.md)
- Or boot everything with containers: [Quickstart (Docker)](quickstart-docker.md)
