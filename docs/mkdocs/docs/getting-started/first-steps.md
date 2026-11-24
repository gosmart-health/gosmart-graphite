# First Steps

A quick tour once the server is running (locally or via
[Docker](quickstart-docker.md)).

## 1. Check system health

Via the CLI:

```bash
uv run gosmart-graphite health
```

or via REST:

```bash
curl http://localhost:8086/api/v1/health
```

This reports Neo4j connectivity, Pykka actor status, and the configured
storage root URI.

## 2. Generate and ingest a mock DICOM study

Create a realistic mock DICOM instance and index it into the graph:

```bash
uv run gosmart-graphite mock --patient-id PT-CLI-001 --modality CT
```

Ingest a local DICOM file (pass `--actor` to dispatch via the Pykka ingestion
actor, mirroring server behavior):

```bash
uv run gosmart-graphite ingest /path/to/file.dcm --actor
```

## 3. Query the graph

```bash
# List indexed patients
uv run gosmart-graphite patients --limit 50

# Run a Cypher query
uv run gosmart-graphite query "MATCH (p:Patient) RETURN p.patient_id LIMIT 10"
```

For query patterns (including FHIR-side queries and the `NOT_` soft-deletion
semantics), see the [Cypher Query Guide](cypher-query-guide.md) and the
[Configuration overview](../configuration/index.md).

## 4. Talk to a remote DICOM node

```bash
# C-ECHO a remote Application Entity
uv run gosmart-graphite echo --called-aet REMOTE_AE --host 192.168.1.100 --port 104

# Send DICOM files to a remote AE via the Store SCU actor
uv run gosmart-graphite send /path/to/dicom/ --dest-aet REMOTE_AE --host 192.168.1.100 --port 104
```

## 5. Administration essentials

On startup the server bootstraps a Platform Administrator from
`GOSMART_GS_INITIAL_ADMIN_*` variables. Useful out-of-band commands:

```bash
# List Platform Administrators
uv run python -m gosmart_graphite.cli.admin list-admins

# Create another Platform Administrator
uv run python -m gosmart_graphite.cli.admin create-admin --username emergency_admin --email emergency@gosmart.health

# Reset a password
uv run python -m gosmart_graphite.cli.admin reset-password --email admin@gosmart.health

# Generate an API key for MCP agents or integrations
uv run python -m gosmart_graphite.cli.admin create-api-key --email admin@gosmart.health \
    --name "Agent Key" --scopes "mcp:full,graph:read"
```

!!! caution "Host-Level Access Control"
    Host-level access (shell, env files, raw Neo4j ports) grants out-of-band
    administrative power. Restrict it to infrastructure administrators.

For superuser bootstrapping details and containerized password recovery via
`GOSMART_GS_FORCE_ADMIN_PASSWORD_RESET`, see
[Bootstrapping & Administration](../configuration/administration.md).

## 6. Drive the server with an AI agent

GRAPHITE ships a built-in [MCP server](../mcp/index.md). Connect Cursor,
Cline, or AntiGravity and try utterances like:

```text
Is the server healthy?
Create a mock CT study for patient PT-DEMO-001.
Send the latest study of PT-CLI-001 to PACS REMOTE_PACS at 192.168.1.50 port 104.
```

See [Utterance Examples](../mcp/utterance-examples.md) for a full catalog.

## 7. CLI Quick Reference

Both `gosmart-graphite` and the shorter alias `gosmart-gs` invoke the same
Typer CLI:

```bash
# Start the FastAPI server directly
uv run gosmart-graphite start --host 0.0.0.0 --port 8086 --reload

# Run standalone MCP server (SSE, StreamableHTTP, or STDIO)
uv run gosmart-graphite mcp --transport sse --port 8001
uv run gosmart-graphite mcp --transport streamable-http --port 8001
uv run gosmart-graphite mcp --transport stdio

# Register or overwrite group-scoped configurations in Neo4j
uv run gosmart-graphite configure dicom-config --file devop-scripts/dicom_config.template.json
uv run gosmart-graphite configure fhir-config --file devop-scripts/fhir_config.template.json
uv run gosmart-graphite configure llm-config --file devop-scripts/llm_config.template.json --assign "GoSmart Health"
```
