# Software Requirements Specification (SRS)

**Document ID:** SRS-GSGS-001  
**Project:** `gosmart-dicom-py-graph-server`  
**Regulatory Standard Alignment:** IEC 62304 Clause 5.2, FDA 21 CFR 820.30, ISO 13485:2016 Clause 7.3  

---

## 1. Scope & Purpose

This document specifies the functional, performance, security, and interface requirements for the **GoSmart DICOM Graph Server & VNA (`gosmart-dicom-py-graph-server`)**. The software functions as a headless, graph-native medical imaging Vendor Neutral Archive (VNA) and Knowledge Graph platform.

The system combines:
1. Standard DICOM Upper Layer network communication (DIMSE: C-STORE, C-FIND, C-GET, C-MOVE, C-ECHO) as a full VNA archive.
2. Property graph database persistence in Neo4j capturing the hierarchical clinical relationships (`Patient -> Study -> Series -> Instance`) and cross-study query topologies.
3. Concurrent, non-blocking ingestion and graph indexing powered by the Pykka actor concurrency system.
4. Embedded Model Context Protocol (MCP) server over Server-Sent Events (SSE) for agentic and external system integration.
5. Headless terminal management via the `gosmart-gs` CLI (with no dependency on client web UIs).
6. Industrial-strength structured logging via `structlog` with arbitrary JSON values and 7-day auto-rotating file handlers.

---

## 2. Functional Requirements (REQ-FUN)

| Requirement ID | Title | Description | Priority |
| :--- | :--- | :--- | :---: |
| **REQ-FUN-001** | DICOM Verification SCP & SCU (C-ECHO) | The system SHALL accept inbound C-ECHO verification requests and SHALL support initiating outbound C-ECHO verification queries to remote DICOM SCPs. | High |
| **REQ-FUN-002** | DICOM Storage SCP (C-STORE) | The system SHALL accept incoming C-STORE transactions for all standard composite DICOM storage SOP classes (CT, MR, X-Ray, PET, Ultrasound, Secondary Capture, RT, GSPS, SR). | High |
| **REQ-FUN-003** | Persistent Storage Root Persistence | The system SHALL persist incoming raw DICOM binary datasets as Part-10 compliant files into the configurable directory identified by `GOSMART_GS_STORAGE_ROOT_URI`. | High |
| **REQ-FUN-004** | Knowledge Graph Schema Indexing | The system SHALL construct and maintain a Neo4j property graph modeling `(:Patient)-[:HAS_STUDY]->(:Study)-[:HAS_SERIES]->(:Series)-[:HAS_INSTANCE]->(:Instance)` with mandatory uniqueness constraints on Patient ID, Study UID, Series UID, and SOP Instance UID. | High |
| **REQ-FUN-005** | DICOM Query SCP (C-FIND) | The system SHALL process C-FIND requests for Patient Root, Study Root, and Modality Worklist information models, executing index lookups against the Neo4j Knowledge Graph and returning standard pending/final responses. | High |
| **REQ-FUN-006** | DICOM Retrieve SCP (C-GET) | The system SHALL process C-GET retrieve requests, retrieving matching SOP instances from `STORAGE_ROOT_URI` and streaming them to the client across the same association via C-STORE sub-operations. | High |
| **REQ-FUN-007** | DICOM Move SCP & SCU (C-MOVE) | The system SHALL process C-MOVE requests, orchestrating transmission of requested instances to designated destination AE Titles via secondary C-STORE SCU associations. | High |
| **REQ-FUN-008** | Pykka Actor Concurrency | The system SHALL utilize the Pykka Actor model (`DicomIngestionActor`) to decouple network and REST ingestion from database synchronization, processing DICOM file indexing in non-blocking background threads. | High |
| **REQ-FUN-009** | REST Ingestion & Inspection Endpoints | The system SHALL provide REST endpoints (`POST /api/v1/dicom/upload`, `POST /api/v1/dicom/mock`, `POST /api/v1/dicom/echo`) supporting synchronous and asynchronous ingestion. | High |
| **REQ-FUN-010** | Graph Query API Endpoints | The system SHALL provide REST endpoints (`GET /api/v1/graph/patients`, `GET /api/v1/graph/patients/{id}`, `POST /api/v1/graph/query`) allowing traversal and arbitrary Cypher execution against Neo4j. | High |
| **REQ-FUN-011** | Model Context Protocol (MCP) Tools | The system SHALL expose standard MCP tools (`health_check`, `generate_mock_dicom`, `ingest_dicom_file`, `list_patients`, `get_patient_graph`, `execute_cypher`, `echo_scu`) accessible via both Server-Sent Events (SSE) and STDIO. | High |
| **REQ-FUN-012** | MCP SSE Mount | The system SHALL mount the MCP server directly onto the FastAPI application at `/mcp/sse` and `/mcp/messages/` for seamless real-time agentic interaction. | High |
| **REQ-FUN-013** | Headless CLI Management (`gosmart-gs`) | The system SHALL provide a command-line interface (`gosmart-gs`) offering commands `start`, `health`, `mock`, `ingest`, `patients`, `query`, `echo`, and `mcp` without requiring any web UI. | High |
| **REQ-FUN-014** | Industrial-Strength Structured Logging | The system SHALL implement structured logging via `structlog` outputting ISO 8601 timestamps, log level, logger name, and arbitrary key-value JSON data. | High |
| **REQ-FUN-015** | 7-Day Auto-Rotating File Logging | The system SHALL automatically rotate log files on a 7-day basis by default (`TimedRotatingFileHandler(when="D", interval=7)`), retaining a configurable number of backup cycles (`GOSMART_GS_LOG_BACKUP_COUNT`, default 8). | High |
| **REQ-FUN-016** | Contextual Log Tracing | The system SHALL support contextvars binding (`bind_log_context`, `clear_log_context`) and SHALL automatically bind `request_id`, `http_method`, `path`, and `client_ip` to all logs emitted within an HTTP request lifecycle. | High |
| **REQ-FUN-017** | Pydantic Schema Validation | The system SHALL validate all REST payloads, configuration variables, and domain entities using Pydantic v2 schemas. | High |
| **REQ-FUN-018** | Standardized Environment Configuration | The system SHALL manage all runtime parameters via Pydantic Settings using the uniform prefix `GOSMART_GS_` with `.env` file fallback. | High |
| **REQ-FUN-019** | Mock DICOM Synthesis | The system SHALL provide on-the-fly synthesis of valid DICOM composite instances for unit testing, integration testing, and simulation without external dependencies. | Medium |
| **REQ-FUN-020** | Health Check & Degradation Resilience | The system SHALL provide a health check endpoint and CLI status report detailing Neo4j, Pykka, and storage status, continuing to run in DEGRADED mode if the graph database is temporarily offline. | High |

---

## 3. Non-Functional & Performance Requirements (REQ-NF)

| Requirement ID | Category | Description | Priority |
| :--- | :--- | :--- | :---: |
| **REQ-NF-001** | Performance | The system SHALL process and index a DICOM instance into the property graph within < 100 milliseconds for standard uncompressed objects. | High |
| **REQ-NF-002** | Concurrency | The system SHALL support at least 20 concurrent inbound DICOM DIMSE associations and 100 concurrent HTTP/MCP client connections without deadlock. | High |
| **REQ-NF-003** | Data Integrity | Raw DICOM binary files SHALL be preserved bit-for-bit without lossy modification of private tags or pixel data during storage. | Critical |
| **REQ-NF-004** | Security | The system SHALL enforce TLS 1.3 encryption for network associations in clinical deployments and protect Protected Health Information (PHI) from plain text logging. | Critical |
| **REQ-NF-005** | Reliability | The system SHALL maintain continuous uptime, preventing server crashes when malformed DICOM files or unparseable network packets are encountered. | High |
