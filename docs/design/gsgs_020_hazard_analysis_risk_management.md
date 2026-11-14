# Hazard Analysis & Software Risk Management Plan

**Document ID:** RMF-GSGS-001  
**Project:** `gosmart-graphite`  
**Regulatory Standard Alignment:** ISO 14971:2019, IEC 62304 Clause 7, FDA SaMD Safety Guidance  

---

## 1. Risk Management Framework

This document provides a comprehensive Software Hazard Analysis for the **Gosmart.Health GRAPHITE (`gosmart-graphite`)** in accordance with **ISO 14971:2019** and **IEC 62304 Clause 7**.

The risk management process assesses clinical, technical, and operational hazards associated with:
1. Ingesting, parsing, and storing DICOM composite objects.
2. Indexing and querying hierarchical graph metadata in Neo4j.
3. Decoupling background tasks via Pykka actor concurrency.
4. DIMSE network operations (C-STORE, C-FIND, C-GET, C-MOVE, C-ECHO).
5. Exposing Model Context Protocol (MCP) tools and CLI interfaces.
6. Industrial structured logging and 7-day auto-rotation.

---

## 2. Hazard Analysis Matrix

| Hazard ID | Hazard Description | Cause / Trigger | Severity | Initial Risk | Software Risk Mitigation (Design Control) | Residual Risk | Verification Method |
| :--- | :--- | :--- | :---: | :---: | :--- | :---: | :--- |
| **HAZ-001** | **Missing Type 1 DICOM Tags** | Ingestion of malformed DICOM object lacking PatientID, StudyInstanceUID, or SOPInstanceUID. | Serious | High | **Design Control:** `DicomService.parse_dataset()` validates required UIDs, assigns deterministic fallbacks where permissible, or rejects corrupt files with clear error status. | Negligible | Unit test `test_dicom_parsing` in `tests/test_api.py`. |
| **HAZ-002** | **Storage Root Disk Exhaustion** | Unbounded continuous DICOM storage fills target storage filesystem. | Critical | High | **Design Control:** Storage path is explicitly configured via `GOSMART_GS_STORAGE_ROOT_URI`. Files are written atomically with SOPInstanceUID naming. System logs warnings before disk capacity limit is reached. | Negligible | Storage path configuration and write verification. |
| **HAZ-003** | **Neo4j Graph Database Disconnection** | Neo4j service becomes temporarily unavailable or network partitions occur. | Serious | Medium | **Design Control:** `Neo4jClient` catches connection exceptions, flags system status as `DEGRADED`, allows raw DICOM persistence to succeed, and avoids crashing the server. | Negligible | Resilient ingestion tests with offline Neo4j (`tests/test_mcp.py`, `tests/test_api.py`). |
| **HAZ-004** | **DIMSE Network Association Drops** | Packet loss or network timeout during large image transfers. | Moderate | Medium | **Design Control:** `pynetdicom` association timeouts and socket handlers safely terminate stalled associations and release system resources without memory leaks. | Negligible | C-ECHO timeout and rejection verification. |
| **HAZ-005** | **Pykka Actor Deadlock or Unhandled Exception** | Worker thread encounters unexpected runtime exception during background ingestion. | Serious | Medium | **Design Control:** `DicomIngestionActor.on_receive()` wraps message processing in robust try/except blocks, records error state, and preserves actor lifecycle. | Negligible | Pykka actor exception handling test in `tests/test_actor.py`. |
| **HAZ-006** | **PHI Leakage in System Logs** | Patient demographic data (Name, SSN, MRN) written indiscriminately to log files. | Critical | High | **Design Control:** `structlog` filters and event renamers sanitize log keys, logging only technical identifiers (PatientID, StudyInstanceUID, SOPInstanceUID) without clinical notes. | Negligible | Log inspection test in `tests/test_logging.py`. |
| **HAZ-007** | **Log Disk Saturation** | Verbose logging fills system drive over prolonged server uptime. | Serious | High | **Design Control:** `TimedRotatingFileHandler` automatically rotates logs every 7 days (`LOG_ROTATION_DAYS=7`) and purges files exceeding `LOG_BACKUP_COUNT=8`. | Negligible | 7-day rotation verification in `tests/test_logging.py`. |
| **HAZ-008** | **Cypher Query Injection** | Malicious or unsanitized user query string injected via API or MCP. | Critical | High | **Design Control:** `GraphService` executes parameterized Cypher queries (`$params`) separating query logic from user-provided inputs. | Negligible | Cypher parameterization tests in `tests/test_mcp.py`. |
| **HAZ-009** | **C-MOVE Destination Loop** | C-MOVE requests move destination back to `GOSMART_GS` causing infinite store loop. | Moderate | Medium | **Design Control:** `PyNetDicomService` validates destination AE Title against local `GOSMART_GS_DICOM_AE_TITLE` and rejects recursive loops. | Negligible | Association validation tests. |
| **HAZ-010** | **Concurrent Ingestion Race Condition** | Identical SOPInstanceUID ingested simultaneously across multiple threads. | Moderate | Medium | **Design Control:** Neo4j uniqueness constraint on `Instance.sop_instance_uid` and idempotent Cypher `MERGE` queries ensure data consistency. | Negligible | Schema constraint verification in `db/neo4j.py`. |

---

## 3. Risk Management Conclusion

All identified hazards (HAZ-001 through HAZ-010) have been mitigated through software design controls. The residual risk for the Gosmart.Health GRAPHITE is assessed as **acceptable** for medical image archiving, knowledge graph querying, and research operations under **ISO 14971:2019**.
