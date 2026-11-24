# Environment Variables (`GOSMART_GS_*`)

Settings are loaded from environment variables and `.env` via
[`Settings`](https://github.com/gosmart-health/gosmart-graphite/blob/main/src/gosmart_graphite/core/config.py).
Every value can also be overridden per-environment without editing `.env`.

## Database (Neo4j)

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_DB_CONNECTION_URI` | `bolt://localhost:7687` | Neo4j connection URI (also accepts `NEO4J_URI`) |
| `GOSMART_GS_DB_USER` | `neo4j` | Neo4j username (also accepts `NEO4J_USERNAME`) |
| `GOSMART_GS_DB_PASSWORD` | `password` | Neo4j password (also accepts `NEO4J_PASSWORD`) |
| `GOSMART_GS_DB_DATABASE` | `neo4j` | Neo4j database name (also accepts `NEO4J_DATABASE`) |
| `GOSMART_GS_DB_MAX_CONNECTION_POOL_SIZE` | `50` | Neo4j maximum connection pool size |
| `GOSMART_GS_DB_MAX_CONNECTION_LIFETIME` | `3600` | Neo4j maximum connection lifetime (seconds) |
| `GOSMART_GS_DB_CONNECT_RETRY_TIMEOUT_SEC` | `600` | Production mode (`GOSMART_GS_DEV_MODE=false`) max total seconds to keep retrying Neo4j connectivity during startup (`0` = retry indefinitely) |
| `GOSMART_GS_DB_CONNECT_RETRY_INITIAL_DELAY_SEC` | `2.0` | Initial exponential backoff delay (seconds) between Neo4j connectivity retries |
| `GOSMART_GS_DB_CONNECT_RETRY_MAX_DELAY_SEC` | `30.0` | Maximum exponential backoff delay (seconds) between Neo4j connectivity retries |

## FHIR

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_FHIR_BASE_URL` | *(empty)* | FHIR server root URL; empty string disables FHIR polling |
| `GOSMART_GS_FHIR_POLL_SEC` | `120` | FHIR polling interval (seconds) for `DiagnosticReport` resources |
| `GOSMART_GS_FHIR_ACCESSION_SYSTEM` | *(empty)* | Identifier system URI for Accession Number |
| `GOSMART_GS_FHIR_PATIENT_ID_SYSTEM` | `urn:oid:2.16.840.1.113883.4.3.25` | Identifier system URI for Patient ID / MRN |

## LLM Configuration Security

LLM providers, models, and endpoints are persisted **dynamically** in Neo4j as
`LLM_Config` nodes — see [LLM Configurations](llm-configurations.md).

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_LLM_CONFIG_ENCRYPTION_KEY` | *(none)* | Optional Fernet key or passphrase for encrypting stored `LLM_Config.api_key` values at rest in Neo4j |

## Clinical NLU

Intent models are trained deterministically from `training/` via `uv run gosmart-clinical-nlu train --training-dir training --out nlu_models/intent_models.zip` (or `./devop-scripts/nlu-train.sh`).

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_NLU_MODEL_PATH` | `./nlu_models/intent_models.zip` | Path to the bundled `gosmart-clinical-nlu` intent models ZIP archive preloaded by `SearchService` |

## Storage

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_STORAGE_ROOT_URI` | `file:///tmp/gosmart_dicom_storage` | Storage root URI (`file://`, `gs://`, `s3://`, `az://`, UNC share) |

Supported URI schemes:

| Backend | Scheme | Example |
|:--------|:-------|:--------|
| Google Cloud Storage | `gs://` | `gs://bucket/imaging/MR.01.dcm` |
| Amazon S3 | `s3://` | `s3://bucket/imaging/MR.01.dcm` |
| Azure Blob Storage | `az://` (or `azure://`, `abfs://`) | `az://dicom-container/mr_studies/MR.01.dcm` |
| Local File | `file://` (or absolute path) | `file:///data/storage/MR.01.dcm` |
| Windows UNC | `\\` | `\\hospital-nas\imaging_share\MR.01.dcm` |


## Server

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_DEV_MODE` | `false` | Enable development mode (`true` relaxes offline Neo4j startup checks; `false` enforces production connectivity retries before failing hard) |
| `GOSMART_GS_HOST` | `0.0.0.0` | FastAPI host binding |
| `GOSMART_GS_PORT` | `8086` | FastAPI HTTP port |
| `GOSMART_GS_RELOAD` | `true` | Uvicorn auto-reload toggle |

## Logging

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `GOSMART_GS_LOG_FORMAT` | `json` | Log format (`json` or `console`) |
| `GOSMART_GS_LOG_DIR` | `./logs` | Directory for rotating log files |
| `GOSMART_GS_LOG_FILE` | `gosmart_gs.log` | Base filename for rotating logs |
| `GOSMART_GS_LOG_ROTATION_DAYS` | `7` | Rotate log files every N days |
| `GOSMART_GS_LOG_BACKUP_COUNT` | `8` | Number of rotated backup log files to retain |
| `GOSMART_GS_LOG_TO_FILE` | `true` | Enable rotating file logging |

## DICOM Network

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_DICOM_AE_TITLE` | `GOSMART_GRAPHITE` | Default local DICOM Application Entity Title |
| `GOSMART_GS_DICOM_SCP_PORT` | `11112` | Default DICOM `C-STORE` / `C-ECHO` SCP port |
| `GOSMART_GS_DICOM_MAX_PDU` | `16382` | Maximum PDU length in bytes |

## Authentication & Security

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_JWT_SECRET_KEY` | *(dev secret)* | Secret key for signing and verifying JWT tokens (override in production) |
| `GOSMART_GS_JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `GOSMART_GS_JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token expiration (minutes) |
| `GOSMART_GS_JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh token expiration (days) |
| `GOSMART_GS_API_KEY_PREFIX` | `gs_live_` | Prefix for generated API keys |
| `GOSMART_GS_GOOGLE_OAUTH_CLIENT_ID` | *(none)* | Google OAuth2 / OIDC Client ID for federated login |
| `GOSMART_GS_GOOGLE_OAUTH_CLIENT_SECRET` | *(none)* | Google OAuth2 Client Secret |

## Admin Bootstrapping

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_INITIAL_ADMIN_EMAIL` | `admin@gosmart.health` | Bootstrap Platform Admin primary email |
| `GOSMART_GS_INITIAL_ADMIN_PASSWORD` | *(none)* | Bootstrap Platform Admin initial password |
| `GOSMART_GS_INITIAL_ADMIN_USERNAME` | `admin` | Bootstrap Platform Admin username |
| `GOSMART_GS_INITIAL_ADMIN_DISPLAY_NAME` | `System Administrator` | Bootstrap Platform Admin display name |
| `GOSMART_GS_FORCE_ADMIN_PASSWORD_RESET` | `false` | Force-reset admin password to `GOSMART_GS_INITIAL_ADMIN_PASSWORD` on startup |

## Audit

| Variable | Default | Description |
|:---------|:--------|:------------|
| `GOSMART_GS_AUDIT_RETENTION_DAYS` | `365` | Audit log retention period in days |
| `GOSMART_GS_AUDIT_LOG_TO_DB` | `true` | Enable Neo4j persistence for audit logs |

## Cloud Storage Credentials (Optional)

The storage service resolves credentials automatically from standard SDK
environment variables (or their `GOSMART_GS_*`-prefixed equivalents):

| Provider | Variables |
|:---------|:----------|
| **GCP** | `GOOGLE_APPLICATION_CREDENTIALS` (JSON key file path), `GOOGLE_CLOUD_PROJECT` / `GCP_PROJECT`, `GCS_ENDPOINT_URL` (emulator host) |
| **AWS** | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_DEFAULT_REGION` / `AWS_REGION`, `AWS_ENDPOINT_URL` |
| **Azure** | `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_ACCOUNT_KEY`, `AZURE_STORAGE_SAS_TOKEN` |
