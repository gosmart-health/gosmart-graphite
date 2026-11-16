# Verification & Validation Plan (VVP)

**Document ID:** VVP-GSGS-001  
**Project:** `gosmart-graphite`  
**Regulatory Standard Alignment:** IEC 62304 Clause 5.5 – 5.7, FDA 21 CFR 820.30(f)(g), FDA Software Validation Guidance  

---

## 1. Scope & Strategy

This document defines the software verification and validation activities for the **Gosmart.Health GRAPHITE (`gosmart-graphite`)**.

The verification strategy encompasses:
1. **Unit Verification**: Automated testing of isolated modules (Pydantic models, DICOM parsing, structlog formatting, Neo4j query builders).
2. **Subsystem Integration Verification**: Testing interactions between subsystems (Pykka actor messaging, Neo4j schema constraint creation, MCP tool execution).
3. **System Verification**: End-to-end testing of DIMSE transactions (C-STORE, C-FIND, C-GET, C-MOVE, C-ECHO), REST APIs, CLI commands, and log rotation schedules.
4. **Resilience & Degradation Verification**: Verifying that the system gracefully handles Neo4j offline conditions without crashing.

---

## 2. Test Execution Environment & Tooling

- **Test Framework**: `pytest` 9.1.1 with `anyio` plugin for asynchronous test coroutines.
- **Python Runtime**: Python 3.14.5.
- **Package & Environment Manager**: Astral `uv` 0.9.x.
- **HTTP Client**: `httpx` and `fastapi.testclient.TestClient`.
- **DICOM Testing Engine**: `pydicom` 3.0.2 and `pynetdicom` 3.0.4.

---

## 3. Verification Protocol Matrix

| Test Case ID | Test Category | Target Subsystem / Requirement | Verification Procedure & Acceptance Criteria | Automated Test Reference |
| :--- | :--- | :--- | :--- | :--- |
| **TC-DCM-001** | Unit | `DicomService` / `REQ-FUN-003`, `019` | Synthesize mock DICOM dataset using `create_mock_dicom()`. Verify that mandatory Type 1 tags (PatientID, StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID) are populated and parse into `DicomHierarchy`. | `tests/test_api.py::test_dicom_parsing` |
| **TC-ACT-001** | Integration | `DicomIngestionActor` / `REQ-FUN-008` | Start `DicomIngestionActor`. Dispatch synchronous `PING` message via `ask()`. Verify actor responds with status `PONG` and processed count 0. | `tests/test_actor.py::test_actor_lifecycle_and_messaging` |
| **TC-ACT-002** | Integration | `DicomIngestionActor` / `REQ-FUN-008` | Send unknown message type to `DicomIngestionActor`. Verify actor catches error, remains alive, and returns error dictionary. Stop actor and verify termination. | `tests/test_actor.py::test_actor_lifecycle_and_messaging` |
| **TC-API-001** | System | Root Endpoint / `REQ-FUN-009`, `018` | Send HTTP GET to `/`. Verify HTTP 200 response containing `version`, `storage_root`, and `db_connection` matching `GOSMART_GS_*` settings. | `tests/test_api.py::test_root_endpoint` |
| **TC-API-002** | System | Health Endpoint / `REQ-FUN-020` | Send HTTP GET to `/api/v1/health`. Verify HTTP 200 response with overall status `HEALTHY` or `DEGRADED`, and individual service status for `neo4j` and `pykka_actor`. | `tests/test_api.py::test_health_endpoint` |
| **TC-LOG-001** | Unit | `structlog` Logging / `REQ-FUN-014`, `016` | Initialize logging with `setup_logging()`. Bind contextvars (`request_id`, `session_user`). Emit log with nested JSON dictionary. Verify log file contains parseable JSON line with all attributes. | `tests/test_logging.py::test_structlog_json_values_and_context` |
| **TC-LOG-002** | System | Log Auto-Rotation / `REQ-FUN-015` | Configure `TimedRotatingFileHandler` with `LOG_ROTATION_DAYS=7` and `LOG_BACKUP_COUNT=8`. Verify handler properties: `when="D"`, interval equals 7 days, and backup count equals 8. | `tests/test_logging.py::test_logging_setup_and_7_day_rotation` |
| **TC-MCP-001** | System | MCP Tool Discovery / `REQ-FUN-011`, `012` | Call `mcp.list_tools()`. Verify that all 7 required tools (`health_check`, `generate_mock_dicom`, `ingest_dicom_file`, `list_patients`, `get_patient_graph`, `execute_cypher`, `echo_scu`) are registered. | `tests/test_mcp.py::test_mcp_list_tools` |
| **TC-MCP-002** | System | MCP Mock Tool Execution / `REQ-FUN-011` | Invoke `mcp.call_tool("generate_mock_dicom", ...)`. Verify that tool executes, saves DICOM file, returns `CallToolResult` with `is_error=False`, and handles offline Neo4j gracefully. | `tests/test_mcp.py::test_mcp_generate_mock_tool` |
| **TC-CLI-001** | System | Headless CLI / `REQ-FUN-013` | Invoke `uv run gosmart-graphite --help` and `uv run gosmart-graphite health`. Verify zero exit code and formatted status table output. | Manual & CI execution. |
| **TC-SYS-001** | Unit & Integration | `SystemService` / `REQ-FUN-021`, `022` | Verify `SystemNode` and `DICOMConfigurationNode` deterministic UUIDv5 derivation, promiscuous mode flag assignment, idempotent bootstrapping with default GoSmart Health metadata, configuration CRUD, soft deletion, and restoration. | `tests/test_system_service.py` |
| **TC-SYS-002** | System | System & Config REST API / `REQ-FUN-023` | Verify `/api/v1/system` (GET, PUT), `/api/v1/system/bootstrap` (POST), and `/api/v1/configurations/dicom` (GET, POST, PUT, DELETE, restore) endpoints. | `tests/test_system_api.py` |
| **TC-SYS-003** | System | System & Config MCP Tools / `REQ-FUN-023` | Verify execution of MCP tools: `get_system_info`, `update_system_info`, `bootstrap_system_defaults`, `list_dicom_configurations`, `get_dicom_configuration`, `create_dicom_configuration`, `update_dicom_configuration`, `delete_dicom_configuration`, `restore_dicom_configuration`. | `tests/test_system_mcp.py` |
| **TC-DCM-002** | System | Dynamic Multi-SCP Lifecycle / `REQ-FUN-024` | Verify dynamic SCP instantiation for `roles=["SCP"]`, AE Title truncation (<= 16 chars), presentation context negotiation filtering, and draining lifecycle transitions on update and removal. | `tests/test_scp_lifecycle.py` |
| **TC-DCM-003** | System | DICOM Query SCP (C-FIND) / `REQ-FUN-005` | Verify C-FIND responses at PATIENT, STUDY, SERIES, and IMAGE levels querying Neo4j Knowledge Graph, yielding `0xFF00` pending matches and final `0x0000` status. | `tests/test_dimse_qr.py` |
| **TC-DCM-004** | System | DICOM Move SCP & Loop Prevention / `REQ-FUN-007`, `REQ-FUN-025` | Verify C-MOVE retrieve sub-operations to destination Storage SCP resolved from Neo4j (:DICOMConfiguration), loop rejection (HAZ-009) when destination equals local AE Title, and unknown destination rejection (0xA801). | `tests/test_dimse_qr.py` |
| **TC-DCM-005** | System | DICOM Retrieve SCP (C-GET) / `REQ-FUN-006` | Verify C-GET retrieve operations streaming matching instances back over the same association via reverse-role presentation context negotiation (`scu_role=True, scp_role=True`). | `tests/test_dimse_qr.py` |
| **TC-LLM-001** | Unit | `LLM_Config` & Secret Utilities / `REQ-FUN-026`, `027` | Verify deterministic UUIDv5 `id` from `tag` (and `test_id`), provider normalization, `dimensions_truncated <= dimensions` validation, `${ENV_VAR}` expansion, Fernet encryption-at-rest, and response masking. | `tests/test_llm_config.py::test_llm_config_deterministic_id_and_validation`, `test_llm_api_key_encryption_env_ref_and_masking` |
| **TC-LLM-002** | Unit & Integration | `LLMConfigService` Hierarchy & Overwrite / `REQ-FUN-026`, `027` | Verify `resolve_target_group()` errors on missing/ambiguous groups, overwrite archiving to `<id>__archived__<hex>` with `NOT_HAS_LLM_CONFIG` links, upward `SUB_GROUP_OF*0..` resolution with `'GoSmart Health'` fallback, and live Neo4j hierarchy resolution. | `tests/test_llm_config.py::test_resolve_target_group_errors_and_success`, `test_upsert_overwrite_clones_archive_and_preserves_groups`, `test_resolve_config_for_group_upward_and_default_fallback`, `test_live_neo4j_hierarchy_resolution_and_overwrite` |
| **TC-LLM-003** | System | LLM CLI, REST API & MCP Tools / `REQ-FUN-028` | Verify `gosmart-graphite configure llm-config` single-object JSON validation and key masking, `/api/v1/configurations/llm` CRUD + RBAC 403 enforcement, and MCP LLM tools + `MCPAuthMiddleware` `X-Group-ID` propagation. | `tests/test_llm_config.py::test_cli_configure_llm_config_validation_and_upsert`, `test_api_llm_configuration_rbac_forbidden`, `test_api_llm_configuration_crud`, `test_mcp_llm_configuration_tools_and_group_middleware` |
| **TC-LLM-004** | Unit & Integration | `EmbeddingService` / `REQ-FUN-028` | Verify dynamic `LLM_Config` resolution, Matryoshka `dimensions_truncated` normalization, Genkit client cache invalidation on config fingerprint change, similarity metrics, and live Gemini/Ollama embedding execution. | `tests/test_vector_service.py`, `tests/test_config.py` |

---

## 4. Acceptance & Release Criteria

A software build is eligible for clearance and release only when:
1. **100% Test Pass Rate**: All automated test cases in the test suite pass with zero errors.
2. **Zero Unmitigated Critical Hazards**: All hazards identified in `gsgs_020_hazard_analysis_risk_management.md` have verified design controls.
3. **100% Traceability**: Every requirement in `gsgs_000_software_requirements_spec.md` traces to a verified test case in `gsgs_040_traceability_matrix.md`.
