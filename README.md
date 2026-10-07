# GoSmart DICOM Graph Server (`gosmart-dicom-py-graph-server`)

A high-performance DICOM Knowledge Graph backend server powered by **Python 3.14**, **FastAPI**, **Pydantic**, **Neo4j**, **pydicom**, **pynetdicom**, and **Pykka**.

---

## 🚀 Features

- **FastAPI & Pydantic**: Async REST API endpoints with automated OpenAPI schemas (`/docs`), strong typing, and validation.
- **Neo4j Knowledge Graph**: Models the DICOM hierarchy `(:Patient)-[:HAS_STUDY]->(:Study)-[:HAS_SERIES]->(:Series)-[:HAS_INSTANCE]->(:Instance)` with uniqueness constraints and indexed lookup.
- **Auditable & Immutable Entity System**: All nodes and relationships derive from `Auditable` featuring deterministic UUIDv5 IDs derived from canonical JSON property subsets, `test_id` telemetry isolation, UTC Cypher timestamps (`CreatedAt`, `ActedAt`, `ExpiresAt`), and immutable action logs (`Action`, `ActedBy`).
- **Reserved `NOT_` Relationship Semantics**: Non-destructive relationship soft-deletion executing atomic property-cloning swaps to `NOT_<rel_type>` within managed Neo4j write transactions.
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
│   ├── health.py           # Health response models
│   └── media.py            # StorageScheme, ParsedStorageUri, MediaMetadata
├── notebooks/              # Exploration and interactive experiments
│   └── exploration.ipynb   # Interactive notebook for pydicom, neo4j, pykka, etc.
├── service/                # Business logic services
│   ├── __init__.py
│   ├── dicom_service.py    # pydicom parsing, mock generation, storage save
│   ├── graph_service.py    # Cypher queries for DICOM hierarchy
│   ├── media_service.py    # Storage-agnostic async media service (Local, UNC, GCS, S3, Azure)
│   └── pynetdicom_service.py # Network AE, C-ECHO SCU verification
├── tests/                  # Test suite
│   ├── assets/mr/          # 10 MR DICOM validation test assets
│   ├── test_actor.py
│   ├── test_api.py
│   └── test_media_service.py # Tests for local files, UNC, GCS, S3, Azure, actors
├── neo4j/                  # Local Neo4j Database (via Docker) Setup
│   ├── docker-compose.yaml
│   └── README.md           # Instructions
├── .env.example            # Environment variables template
├── .env                    # Local environment configuration
├── main.py                 # Application factory & Uvicorn runner
├── pyproject.toml          # uv project definition & dependencies
└── README.md
```

---

## ☁️ Storage Agnostic Media Service (`media_service.py`)

`gosmart-dicom-py-graph-server` includes a unified, storage-agnostic `MediaService` (`service/media_service.py`) that abstracts file operations across local storage, Windows network shares, and multi-cloud providers.

### Supported URI Schemes

| Provider / Target | URI Scheme Prefix | Example URI |
| :--- | :--- | :--- |
| **Google Cloud Storage** | `gs://` (or `gcs://`) | `gs://my-dicom-bucket/mr_studies/MR.01.dcm` |
| **Amazon S3** | `s3://` (or `s3a://`) | `s3://my-dicom-bucket/mr_studies/MR.01.dcm` |
| **Azure Blob Storage** | `az://` (or `azure://`, `abfs://`) | `az://dicom-container/mr_studies/MR.01.dcm` |
| **Local File** | `file://` (or absolute path) | `file:///data/storage/MR.01.dcm` |
| **Windows UNC** | Denoted by backslash (`\\` or `\`) | `\\hospital-nas\imaging_share\MR.01.dcm` |

### Cloud Credentials (SDK Standard Labels)

The service automatically resolves credentials from standard environment variables:
- **GCP**: `GOOGLE_APPLICATION_CREDENTIALS` (JSON key file path), `GOOGLE_CLOUD_PROJECT` / `GCP_PROJECT`, `GCS_ENDPOINT_URL` (emulator host)
- **AWS**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_DEFAULT_REGION` / `AWS_REGION`, `AWS_ENDPOINT_URL`
- **Azure**: `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_ACCOUNT_KEY`, `AZURE_STORAGE_SAS_TOKEN`

### Asynchronous & Actor Execution

All core operations in `media_service` (`read`, `write`, `exists`, `delete`, `stat`, `list_files`, `copy`, `move`, `read_dicom`, `write_dicom`) are asynchronous (`async def`). For Pykka actors running on worker threads (e.g. `pykka.ThreadingActor`) or synchronous scripts, `run_sync()` and sync method equivalents (`read_sync`, `write_sync`, etc.) are provided without event loop deadlocks.

---

## ⚙️ Environment Variables (`GOSMART_GS_*`)

Configured via environment variables or `.env`:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `GOSMART_GS_DB_CONNECTION_URI` | `bolt://localhost:7687` | Neo4j Connection URI |
| `GOSMART_GS_DB_USER` | `neo4j` | Neo4j Username |
| `GOSMART_GS_DB_PASSWORD` | `password` | Neo4j Password |
| `GOSMART_GS_DB_DATABASE` | `neo4j` | Neo4j Database Name |
| `GOSMART_GS_STORAGE_ROOT_URI` | `file:///tmp/gosmart_dicom_storage` | Storage root URI (file://, gs://, s3://, az://, azure://, \\\\unc\\share) |

| `GOSMART_GS_HOST` | `0.0.0.0` | FastAPI host binding |
| `GOSMART_GS_PORT` | `8000` | FastAPI HTTP port |
| `GOSMART_GS_RELOAD` | `true` | Uvicorn auto-reload |
| `GOSMART_GS_LOG_LEVEL` | `INFO` | Server logging level |
| `GOSMART_GS_DICOM_AE_TITLE` | `GOSMART_GS` | Local DICOM Application Entity Title |
| `GOSMART_GS_DICOM_SCP_PORT` | `11112` | DICOM C-STORE / C-ECHO SCP Port |
| `GOSMART_GS_AUDIT_RETENTION_DAYS` | `365` | Audit log retention period in days (default: 1 year) |
| `GOSMART_GS_AUDIT_LOG_TO_DB` | `true` | Enable Neo4j database persistence for audit logs |


---

## 🛠️ Getting Started

### 1. Installation

The environment is already managed using `uv` with Python 3.14:

```bash
uv sync
```

### 2. Run the Servers

NOTE: Before running the server you may issue the following Cypher to clear the entire
database. This will initialize the system to its default, then you can add remote AEs
etc after the server starts via the REST scripts (under test/rest-client) with the
REST Client on VS Code.

```cypher
MATCH (n) DETACH DELETE n;
```

```bash
uv run python main.py
# or directly with uvicorn
uv run uvicorn main:app --reload --port 8000
```

Interactive Swagger documentation will be available at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### 3. System & DICOM Configurations API & MCP

The server automatically bootstraps an immutable root `(:System)` node linked to a primary promiscuous DICOM entry (`ae_title = "*"`):

#### REST Endpoints:
- `GET /api/v1/system` — Inspect System metadata (`name`, `owner`, `address`, `email`, `phone`, `serial_number`, `installed_at`) and attached configurations.
- `PUT /api/v1/system` — Installer update of mutable System properties.
- `POST /api/v1/system/bootstrap` — Idempotent environment bootstrap.
- `POST /api/v1/system/test/cleanup` — Full test data cleanup endpoint to detach and delete test nodes across target labels (`System`, `DICOMConfiguration`, `Instance`, `Patient`, `Series`, `Study`, etc.) where `test_id` is defined and non-empty; also drains associated ephemeral test SCP instances.
- `GET /api/v1/configurations/dicom` — Query active configurations (filters: `role`, `is_promiscuous`, `include_tests`).
- `POST /api/v1/configurations/dicom` — Register a new DICOMConfiguration node and dynamically instantiate SCP if role is SCP.
- `GET /api/v1/configurations/dicom/{id}` — Retrieve single configuration.
- `PUT /api/v1/configurations/dicom/{id}` — Update configuration properties; maintains in-flight association threads while launching updated SCP instance.
- `DELETE /api/v1/configurations/dicom/{id}` — Soft delete (sets `Action="DELETE"` and negates relationship to `NOT_HAS_DICOM_CONFIGURATION`); maintains in-flight associations until done.
- `POST /api/v1/configurations/dicom/{id}/restore` — Restore soft-deleted configuration and re-activate SCP.
- `GET /api/v1/configurations/dicom-instances` — Inspect live runtime status and active association counts for all running and draining SCP instances.
- `GET /api/v1/configurations/dicom-instances/{id}` — Inspect runtime status of a specific SCP instance.

#### Dynamic SCP Lifecycle & Presentation Context Enforcement:
- **Added**: Instantiates and launches a new SCP instance dynamically on its configured host, port, and AE title.
- **Changed**: Drains and preserves existing association threads uninterrupted while launching a new SCP instance with the updated configuration.
- **Removed**: Transitions the SCP to draining state (stops accepting new associations while letting active transfer threads complete).
- **Service Class & Transfer Syntax Enforcement**: Strictly negotiates presentation contexts according to configured `dicom_services` (SOP Classes) and `transfer_syntaxes`, rejecting unconfigured services or syntax during association negotiation.

#### MCP Administrative Tools:
- `get_system_info`, `update_system_info`, `bootstrap_system_defaults`
- `list_dicom_configurations`, `get_dicom_configuration`, `create_dicom_configuration`, `update_dicom_configuration`, `delete_dicom_configuration`, `restore_dicom_configuration`

### 4. Run Tests

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
