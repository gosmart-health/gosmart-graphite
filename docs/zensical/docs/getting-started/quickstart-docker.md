# Quickstart (Docker)

Docker provides the fastest path to a full stack running both **Gosmart.Health
GRAPHITE** and **Neo4j**.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- *(Optional)* Copy `.env.example` to `.env` and customize values before building:

  ```bash
  cp .env.example .env
  ```

## Option A: Full-Stack Launch (Recommended)

From the repository root, the bundled `docker/docker-compose.yml` orchestrates
both **Gosmart.Health GRAPHITE** and **Neo4j**:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

### Services started

| Service | Container name | Ports | Description |
|:--------|:---------------|:------|:------------|
| **Graph Server** | `gosmart-graphite` | `8086` (HTTP), `11112` (DICOM SCP) | FastAPI REST API, MCP endpoints, DICOM SCP |
| **Neo4j** | `gosmart-neo4j` | `7474` (Browser), `7687` (Bolt) | Graph database with health check |

### Service endpoints after launch

| Endpoint | URL |
|:---------|:----|
| Swagger UI | [http://localhost:8086/docs](http://localhost:8086/docs) |
| ReDoc | [http://localhost:8086/redoc](http://localhost:8086/redoc) |
| Health check | [http://localhost:8086/api/v1/health](http://localhost:8086/api/v1/health) |
| MCP StreamableHTTP | [http://localhost:8086/mcp](http://localhost:8086/mcp) |
| MCP SSE | [http://localhost:8086/sse](http://localhost:8086/sse) |
| Neo4j Browser | [http://localhost:7474](http://localhost:7474) |
| DICOM SCP | `GOSMART_GS@localhost:11112` (configured in `docker/docker-compose.yml`) |

### Persistent named volumes

- `neo4j_data` — Neo4j database files
- `neo4j_logs` — Neo4j log files
- `dicom_storage` — Persisted DICOM files (instances received via `C-STORE`)
- `server_logs` — Application log files with auto-rotation

### Stop or reset the stack

```bash
# Stop containers while preserving persistent volumes
docker compose -f docker/docker-compose.yml down

# Stop containers and remove all persistent volumes
docker compose -f docker/docker-compose.yml down -v
```

### Overriding environment variables

`docker/docker-compose.yml` provides local development defaults. To override
variables, either:

1. Edit the `environment:` section in `docker/docker-compose.yml`, or
2. Pass an external `.env` file with `--env-file`:

   ```bash
   docker compose -f docker/docker-compose.yml --env-file .env up -d
   ```

!!! tip "Internal Compose Networking"
    Inside Docker Compose, the Graph Server reaches Neo4j over the Compose
    network at `bolt://neo4j:7687`.

## Option B: Build Standalone Image

Build the Docker image independently:

```bash
docker build -t gosmart-graphite -f docker/Dockerfile .
```

Run standalone against an external Neo4j instance:

```bash
docker run -d \
  --name gosmart-graphite \
  -p 8086:8086 \
  -p 11112:11112 \
  -e GOSMART_GS_DB_CONNECTION_URI=bolt://host.docker.internal:7687 \
  -e GOSMART_GS_DB_PASSWORD=your_password \
  -e GOSMART_GS_INITIAL_ADMIN_PASSWORD=MySecurePassword123! \
  gosmart-graphite
```

### Dockerfile architecture

`docker/Dockerfile` uses a **multi-stage build** for a small image footprint and
hardened execution:

| Stage | Base image | Purpose |
|:------|:-----------|:--------|
| **Builder** | `python:3.14-slim` | Installs `uv`, compiles C-extensions, and syncs locked production dependencies |
| **Runner** | `python:3.14-slim` | Minimal runtime with `curl` for health checks, running as `appuser` (UID/GID `10001`) |

Key design choices:

- **Layer caching** — `pyproject.toml` and `uv.lock` are copied first so
  dependency installs remain cached when only source files change.
- **Unprivileged user** — The runtime container executes as `appuser:appuser`
  (`10001:10001`), never `root`.
- **Health check** — Built-in `HEALTHCHECK` polls `GET /api/v1/health` every 30
  seconds.
- **Exposed ports** — `8086` (HTTP API) and `11112` (DICOM SCP).

## Option C: Neo4j-Only Docker (Local Development)

When running the FastAPI server natively with `uv` (see
[Installation](installation.md)), you can run Neo4j alone in Docker:

```bash
docker compose -f neo4j-local-docker/docker-compose.yaml up -d
```

See [`neo4j-local-docker/README.md`](https://github.com/gosmart-health/gosmart-graphite/blob/main/neo4j-local-docker/README.md)
for details.

## Next steps

- Verify the install and ingest your first study: [First Steps](first-steps.md)
- Superuser bootstrapping & containerized password reset: [Bootstrapping & Administration](../configuration/administration.md)
- Connect an AI agent via MCP: [MCP Overview](../mcp/index.md)
