# Quickstart (Docker)

Docker provides the fastest path to a full stack running both the **Graph
Server** and **Neo4j**.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- *(Optional)* Copy `.env.example` to `.env` and customize values before building:

  ```bash
  cp .env.example .env
  ```

## Launch the full stack

From the repository root, the bundled `docker/docker-compose.yml` orchestrates
both services:

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

### Services started

| Service          | Container name     | Ports                               | Description                                 |
|:-----------------|:-------------------|:------------------------------------|:--------------------------------------------|
| **Graph Server** | `gosmart-graphite` | `8086` (HTTP), `11112` (DICOM SCP)  | FastAPI REST API, MCP endpoints, DICOM SCP  |
| **Neo4j**        | `gosmart-neo4j`    | `7474` (Browser), `7687` (Bolt)     | Graph database with health check            |

### Service endpoints after launch

| Endpoint | URL |
|:---------|:----|
| Swagger UI | [http://localhost:8086/docs](http://localhost:8086/docs) |
| ReDoc | [http://localhost:8086/redoc](http://localhost:8086/redoc) |
| Health check | [http://localhost:8086/api/v1/health](http://localhost:8086/api/v1/health) |
| MCP StreamableHTTP | [http://localhost:8086/mcp](http://localhost:8086/mcp) |
| MCP SSE | [http://localhost:8086/sse](http://localhost:8086/sse) |
| Neo4j Browser | [http://localhost:7474](http://localhost:7474) (default credentials `neo4j`/`neo4j`) |
| DICOM SCP | `GOSMART_GS@localhost:11112` |

Named volumes for persistent data:

- `neo4j_data` — Neo4j database files
- `neo4j_logs` — Neo4j log files
- `dicom_storage` — Persisted DICOM files (instances received via `C-STORE`)

## Prepare the storage root

By default the server stores DICOM files under `/tmp/gosmart_dicom_storage`.
Make sure the directory exists and is writable by the Docker user (typically
the user who launches the `docker compose` commands):

```bash
mkdir -p /tmp/gosmart_dicom_storage
```

## Check the database

Open [http://localhost:7474](http://localhost:7474) in your browser to launch
the Neo4j Browser UI and use `neo4j` as both the username and password on first
connect. Docker manages the database volume, so data survives container
restarts.

## Next steps

- Verify the install and ingest your first study: [First Steps](first-steps.md)
- Connect an AI agent via MCP: [MCP Overview](../mcp/index.md)
