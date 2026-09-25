# GoSmart DICOM Graph Server (`gosmart-dicom-py-graph-server`)

A high-performance DICOM Knowledge Graph backend server powered by **Python 3.14**, **FastAPI**, **Pydantic**, **Neo4j**, **pydicom**, **pynetdicom**, and **Pykka**.

---

## 🚀 Features

- **FastAPI & Pydantic**: Async REST API endpoints with automated OpenAPI schemas (`/docs`), strong typing, and validation.
- **Neo4j Knowledge Graph**: Models the DICOM hierarchy `(:Patient)-[:HAS_STUDY]->(:Study)-[:HAS_SERIES]->(:Series)-[:HAS_INSTANCE]->(:Instance)` with uniqueness constraints and indexed lookup.
- **pydicom & pynetdicom**: DICOM header parsing, file persistence, mock dataset generation, and network operations (C-ECHO SCU / Verification).
- **Pykka Concurrency**: Actor model (`DicomIngestionActor`) for non-blocking asynchronous DICOM ingestion and background processing.
- **UV & Python 3.14**: Managed with Astral `uv` on Python 3.14.5.
- **Interactive Exploration Notebook**: Ready-to-run Jupyter notebook exploring all library functions.

---

## 📁 Project Structure

```text
gosmart-dicom-py-graph-server/
├── actor/                  # Pykka Actor system
│   ├── __init__.py
│   └── dicom_actor.py      # Background ingestion & processing actor
├── api/                    # FastAPI routes & endpoints
│   ├── __init__.py
│   └── v1/
│       ├── endpoints/
│       │   ├── dicom.py    # Ingestion, mock generator, and C-ECHO endpoints
│       │   ├── graph.py    # Graph traversal and Cypher query endpoints
│       │   └── health.py   # Health status (Neo4j, Pykka, Storage)
│       └── router.py       # Aggregated v1 API router
├── core/                   # Application settings & logging
│   ├── __init__.py
│   ├── config.py           # Pydantic Settings with GOSMART_GS_ prefix
│   └── logging.py          # Structured logging configuration
├── db/                     # Database connections
│   ├── __init__.py
│   └── neo4j.py            # Neo4j async client, pooling, schema constraints
├── model/                  # Pydantic domain models
│   ├── __init__.py
│   ├── dicom.py            # Patient, Study, Series, Instance models
│   ├── graph.py            # Cypher and graph schema models
│   └── health.py           # Health response models
├── notebooks/              # Exploration and interactive experiments
│   └── exploration.ipynb   # Interactive notebook for pydicom, neo4j, pykka, etc.
├── service/                # Business logic services
│   ├── __init__.py
│   ├── dicom_service.py    # pydicom parsing, mock generation, storage save
│   ├── graph_service.py    # Cypher queries for DICOM hierarchy
│   └── pynetdicom_service.py # Network AE, C-ECHO SCU verification
├── tests/                  # Test suite
│   ├── test_actor.py
│   └── test_api.py
├── .env.example            # Environment variables template
├── .env                    # Local environment configuration
├── main.py                 # Application factory & Uvicorn runner
├── pyproject.toml          # uv project definition & dependencies
└── README.md
```

---

## ⚙️ Environment Variables (`GOSMART_GS_*`)

Configured via environment variables or `.env`:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `GOSMART_GS_DB_CONNECTION_URI` | `bolt://localhost:7687` | Neo4j Connection URI |
| `GOSMART_GS_DB_USER` | `neo4j` | Neo4j Username |
| `GOSMART_GS_DB_PASSWORD` | `password` | Neo4j Password |
| `GOSMART_GS_DB_DATABASE` | `neo4j` | Neo4j Database Name |
| `GOSMART_GS_STORAGE_ROOT_URI` | `file:///tmp/gosmart_dicom_storage` | Storage root URI for raw DICOM files |
| `GOSMART_GS_HOST` | `0.0.0.0` | FastAPI host binding |
| `GOSMART_GS_PORT` | `8000` | FastAPI HTTP port |
| `GOSMART_GS_RELOAD` | `true` | Uvicorn auto-reload |
| `GOSMART_GS_LOG_LEVEL` | `INFO` | Server logging level |
| `GOSMART_GS_DICOM_AE_TITLE` | `GOSMART_GS` | Local DICOM Application Entity Title |
| `GOSMART_GS_DICOM_SCP_PORT` | `11112` | DICOM C-STORE / C-ECHO SCP Port |

---

## 🛠️ Getting Started

### 1. Installation

The environment is already managed using `uv` with Python 3.14:

```bash
uv sync
```

### 2. Run the Server

```bash
uv run python main.py
# or directly with uvicorn
uv run uvicorn main:app --reload --port 8000
```

Interactive Swagger documentation will be available at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### 3. Run Tests

```bash
uv run pytest
```

### 4. Interactive Notebook

Open and execute [notebooks/exploration.ipynb](file:///Users/manabutokunaga/development/dicom-root/gosmart-dicom-py-graph-server/notebooks/exploration.ipynb) in your editor or start Jupyter:

```bash
uv run jupyter lab notebooks/
```

The notebook guides you through:
1. Environment and dependency verification.
2. Configuration loading with `GOSMART_GS_*` prefix.
3. In-memory DICOM generation and parsing with `pydicom` into Pydantic models.
4. Background Actor dispatch with `pykka` (`tell` and `ask`).
5. Graph queries and connectivity with `neo4j`.
6. DICOM Application Entity (AE) setup and C-ECHO SCU ping with `pynetdicom`.
