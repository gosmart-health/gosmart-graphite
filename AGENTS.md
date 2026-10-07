# Agent Instructions & Standard Rules

These instructions and rules apply to all AI coding agents working within this repository.

## 1. Documentation & Usage Updates

If usage changes—including command-line options, environment variables, configuration settings, or MCP capabilities/tools:

- Review the design and reference documents under [`./docs`](./docs) (such as software requirements, system design, and verification plans) as well as [`README.md`](./README.md).
- Update and add all relevant details, parameters, descriptions, and examples to keep documentation in sync with codebase behavior.

## 2. Code Quality & Linting Gate

- Do not commit or finalize code that does not pass the Ruff lint and formatting checks.
- Always run and verify:

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  ```

  Ensure all issues are resolved and all checks pass prior to committing or concluding changes.

## 3. Implementations

- Always write an Implementation Plan (ImplementationPlan.md) usable for copying in GitHub Issues before implementing and wait for approval.
- Avoid making assumptions if you are not certain, suggest alternatives and ask for inputs.
- Test driven design, always write modular test. Do not rely on previous test states.
- Only delete data that test cases has created so that that tests can be run even in production without colliding existing data, use `test_id` in database properties
  and add a test session identifier or simply a "test" or a Test Case Name. We keep the test data after a run is complete and only clear them before a test for
  post-test completion or failure manual inspections and recording.

### Architectural Policies

- The system uses Actor Model for multi-threaded processes like DICOM network, file or object storage, retrieval. Always use the Actor pattern where there 
  will be background thread and blocking may occur. 
- Strictly implement record generation idempotency using deterministic `id`
- Most database entity structures should be derived from Auditable class which contain essential field needed for audits

## 4. Testing

- If testing is looping more than 3 times without conversing to a resolution, stop the test, explain what you were going to resolve, and ask for user inputs.
- Remove test generated data (i.e., test_id property is defined) before the test not after the test so that user can inspect the test post results.
