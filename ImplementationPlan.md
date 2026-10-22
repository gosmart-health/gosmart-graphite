# Implementation Plan: Update Neo4j Docker Compose to use Docker Managed Volume

## Problem Statement
`neo4j/docker-compose.yaml` currently specifies a host bind mount (`/usr/local/neo4j/data:/data`). This requires manual host directory creation, OS-specific path adjustments, and explicit file-sharing permission setup in Docker Desktop.

## Proposed Changes
1. **Update `neo4j/docker-compose.yaml`**:
   - Replace the host bind mount `/usr/local/neo4j/data:/data` with a named Docker managed volume `neo4j_data:/data`.
   - Add the top-level `volumes:` section defining `neo4j_data:`.
2. **Update `neo4j/README.md`**:
   - Update instructions to reflect automatic Docker managed volume persistence instead of manual host path configuration.

## Verification Plan
1. Validate compose syntax with `docker compose -f neo4j/docker-compose.yaml config`.
2. Run Ruff quality gates (`uv run ruff check .` and `uv run ruff format --check .`).
