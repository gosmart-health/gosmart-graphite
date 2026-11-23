# Getting Started

There are two supported ways to run GRAPHITE:

| Path | Best for | Guide |
|:-----|:---------|:------|
| **Docker Compose (full stack)** | Fastest evaluation — starts GRAPHITE *and* Neo4j together | [Quickstart (Docker)](quickstart-docker.md) |
| **Local development with `uv`** | Day-to-day development against an existing Neo4j | [Installation](installation.md) |

Either way, finish with [First Steps](first-steps.md) to verify the server,
generate your first study, and meet the CLI.

## Prerequisites

| Requirement | Notes |
|:------------|:------|
| **Python 3.14+** | Dependencies and virtual environments are managed with [uv](https://docs.astral.sh/uv/). |
| **uv** | Install via `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows). |
| **Neo4j 5.x** | Local install, [Neo4j Aura Free](https://neo4j.com/cloud/aura-free/), or the bundled Docker service. Bolt on `7687`. |
| **Docker Desktop** | Only needed for the Docker quickstart and the GoSmart testing ecosystem. |
| **(Optional) Ollama** | Hosts local embedding/generation models for the LLM-powered features. |

## At a glance

```bash
# 1. Clone and install
git clone https://github.com/gosmart-health/gosmart-graphite.git
cd gosmart-graphite
uv sync

# 2. Configure
cp .env.example .env          # then edit GOSMART_GS_* values
./devop-scripts/scaffold_configs.sh   # register starter DICOM/FHIR/LLM configs

# 3. Run (local)
./start.sh                    # Windows: .\start.ps1
```

The API is then available at [http://localhost:8086](http://localhost:8086)
with interactive docs at [/docs](http://localhost:8086/docs) (Swagger UI) and
[/redoc](http://localhost:8086/redoc) (ReDoc).

> [!TIP]
> Prefer containers? Jump straight to the [Docker quickstart](quickstart-docker.md)
> which boots both GRAPHITE and Neo4j with one command.
