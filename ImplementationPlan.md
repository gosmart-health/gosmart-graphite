# Implementation Plan: Normalize `test_id` and Prevent Unintended SCP Termination

## Problem Statement
When a user submits a DICOM configuration with an empty string `test_id` (e.g., `"test_id": ""`), the backend treats it as a non-null filter string. In Cypher:
```cypher
WHERE ($test_id IS NULL AND (s.test_id IS NULL OR s.test_id = ''))
   OR ($test_id IS NOT NULL AND s.test_id = $test_id)
```
When `$test_id = ""`, the condition evaluates strictly to `s.test_id = ""`. Nodes created with `test_id = null` (including the root `System` node and the default promiscuous SCP node `GOSMART_GS` on port `11112`) are excluded. This triggers:
1. Re-bootstrapping with an empty configuration list.
2. `pynetdicom_service.sync_from_configurations([])` terminating all active SCP servers on port `11112`.
3. If the newly added configuration specifies a remote IP with `roles: ["SCP"]`, local socket bind fails (`Errno 49 Can't assign requested address`), leaving zero DICOM listeners active.

## Proposed Changes

### 1. Model Validation (`model/system.py`)
- Add field validator for `test_id` across `DICOMConfigurationCreateRequest`, `DICOMConfigurationUpdateRequest`, and `SystemUpdateRequest` that normalizes empty/whitespace strings to `None`.

### 2. Service-Layer Normalization (`service/system_service.py`)
- In `SystemService` methods (`bootstrap_system`, `list_configurations`, `create_configuration`, `update_configuration`, `cleanup_test_data`), ensure `test_id = test_id.strip() if (test_id and test_id.strip()) else None`.
- Ensure Cypher queries treat `$test_id = ""` identically to `$test_id IS NULL`.

### 3. Automated Tests (`tests/test_system_service.py` & `tests/test_system_api.py`)
- Test adding a configuration with `"test_id": ""` and verify:
  - It links to the default root System node (not creating an orphaned system).
  - The default promiscuous SCP on port `11112` remains active and is not stopped.

### 4. Verification
- Run test suite: `uv run pytest`.
- Run Ruff quality gates: `uv run ruff check .` and `uv run ruff format --check .`.
- Restart live server and verify port `11112` is actively listening on `0.0.0.0`.
