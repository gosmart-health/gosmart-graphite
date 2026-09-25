# Design Controls & Regulatory Documentation

This directory contains the regulatory-compliant Design Controls documentation suite for the **GoSmart DICOM Graph Server & VNA (`gosmart-dicom-py-graph-server`)**, aligned with **FDA 21 CFR 820.30**, **IEC 62304:2006+A1:2015**, **ISO 14971:2019**, and **ISO 13485:2016 Clause 7.3**.

The design documents are prefixed with `gsgs_XXX` in logical order (from requirements to architecture, risk management, verification & validation, traceability, and cybersecurity).

---

> [!IMPORTANT]
> **Regulatory Intent & Quality Management Scope**
> 
> The GoSmart DICOM Graph Server is engineered with rigorous medical device design controls, safety architecture, and verification traceability. The structure and content of these documents provide turnkey artifacts for medical device manufacturers, hospital IT integrators, and clinical research organizations seeking to adopt, validate, and clear this software within their own certified Quality Management System (QMS) and 510(k) submissions.

---

## Document Walkthrough Index

| Document | Regulatory Standard | Description |
| :--- | :--- | :--- |
| **[gsgs_000_software_requirements_spec.md](./gsgs_000_software_requirements_spec.md)** | IEC 62304 Cl. 5.2 / FDA 21 CFR 820.30 | **Software Requirements Specification (SRS)**: Functional, performance, and interface requirements for DICOM VNA operations (C-STORE, C-FIND, C-GET, C-MOVE, C-ECHO), Neo4j knowledge graph schema, Pykka actor concurrency, MCP tools over SSE, headless CLI, and 7-day auto-rotating structured logging. |
| **[gsgs_010_system_design_specification.md](./gsgs_010_system_design_specification.md)** | IEC 62304 Cl. 5.3 & 5.4 / FDA 21 CFR 820.30 | **System Design Specification (SDS / Architecture)**: Subsystem decomposition (`core`, `db`, `model`, `service`, `actor`, `api`, `cli`), Neo4j graph schema, Pykka actor messaging lifecycle, DIMSE network handling, and MCP SSE server mount. |
| **[gsgs_020_hazard_analysis_risk_management.md](./gsgs_020_hazard_analysis_risk_management.md)** | ISO 14971:2019 / IEC 62304 Cl. 7 | **Hazard Analysis & Risk Management Plan**: Software Risk Matrix identifying clinical and technical hazards (data loss, graph inconsistency, race conditions, PHI leakage, log disk saturation) and corresponding software design mitigations. |
| **[gsgs_030_verification_and_validation_plan.md](./gsgs_030_verification_and_validation_plan.md)** | IEC 62304 Cl. 5.5 - 5.7 | **Verification & Validation Plan (VVP)**: Verification protocols across automated unit tests (`pytest`), integration tests, DIMSE conformance tests, graph topology verification, Pykka concurrency stress tests, and 7-day log rotation tests. |
| **[gsgs_040_traceability_matrix.md](./gsgs_040_traceability_matrix.md)** | FDA Design Controls | **Requirements Traceability Matrix (RTM)**: Bi-directional traceability mapping **Requirements (SRS) <-> System Architecture (SDS) <-> Hazards (ISO 14971) <-> Verification Tests (V&V)**. |
| **[gsgs_050_cybersecurity_and_soup_bom.md](./gsgs_050_cybersecurity_and_soup_bom.md)** | FDA Cybersecurity Guidance / IEC 81001-5-1 | **Cybersecurity & SOUP BOM**: Software Bill of Materials (SBOM) for SOUP components (`pydicom`, `pynetdicom`, `neo4j`, `pykka`, `fastapi`, `structlog`, `mcp`), threat modeling, PHI protection, and vulnerability management procedures. |
| **[dicom_conformance_statement.md](../dicom_conformance_statement.md)** | NEMA PS 3.2 | **DICOM Conformance Statement (DCS)**: Official NEMA PS 3.2 statement specifying supported SOP Classes, Transfer Syntaxes, DIMSE services (C-STORE, C-FIND, C-GET, C-MOVE, C-ECHO), and network profiles. |
