# MCP Overview (AI Agents)

GRAPHITE implements the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/),
letting AI agents (Cursor, Cline, AntiGravity, ...) operate the server through
natural language: query patients, ingest studies, manage configurations, and
route DICOM transfers.

## Transports & endpoints

| Transport | Endpoint / command | Notes |
|:----------|:-------------------|:------|
| **StreamableHTTP** | `http://localhost:8086/mcp` | Recommended for high-throughput clients |
| **SSE** | `http://localhost:8086/sse` | Server-Sent Events |
| **STDIO** | `gosmart-graphite mcp --transport stdio` | Runs MCP in-process over stdio |

The MCP server also runs standalone with a chosen transport:

```bash
uv run gosmart-graphite mcp --transport sse --port 8001
uv run gosmart-graphite mcp --transport streamable-http --port 8001
uv run gosmart-graphite mcp --transport stdio
```

## Authentication

Protected REST and MCP endpoints accept either:

- **API Key** — `X-API-Key: gs_live_...` header, `Authorization: Bearer gs_live_...`,
  or an `?api_key=gs_live_...` query parameter for MCP HTTP/SSE URLs.
- **JWT access token** — `Authorization: Bearer <access_token>` obtained from
  `/api/v1/auth/login` or `/api/v1/auth/oauth/google`.

For STDIO transports, pass the key through the `GOSMART_MCP_API_KEY`
environment variable.

### Obtaining an API key

Option A — administrative CLI (out-of-band):

```bash
uv run python -m gosmart_graphite.cli.admin create-api-key --email admin@gosmart.health --name "Local Agent Key"

# With custom scopes and expiration
uv run python -m gosmart_graphite.cli.admin create-api-key --email admin@gosmart.health \
    --name "AntiGravity Agent Key" --scopes "mcp:full,graph:read" --expires-days 90
```

Option B — REST API: log in to `POST /api/v1/auth/login`, then
`POST /api/v1/auth/api-keys` with `Authorization: Bearer <ACCESS_TOKEN>` and a
JSON body `{"user_id": "...", "name": "Cursor Agent Key", "scopes": ["*"]}`.
Save the returned `raw_api_key` — it is shown only once.

> [!IMPORTANT]
> Configuration-changing tools (`create_*`, `update_*`, `delete_*`,
> `upsert_*`, assign/dissociate, `bootstrap_system_defaults`,
> `update_system_info`) require the caller to hold the **PLATFORM_ADMIN**
> role; otherwise the tool returns a permission error.

## Client setup

### Cursor

`~/.cursor/mcp.json` (or workspace-level `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "gosmart-graphite": {
      "url": "http://127.0.0.1:8086/sse?api_key=gs_live_YOUR_API_KEY_HERE"
    }
  }
}
```

STDIO variant:

```json
{
  "mcpServers": {
    "gosmart-graphite": {
      "command": "uv",
      "args": ["run", "gosmart-graphite", "mcp", "--transport", "stdio"],
      "env": { "GOSMART_MCP_API_KEY": "gs_live_YOUR_API_KEY_HERE" }
    }
  }
}
```

### Cline

`~/.cline/data/settings/cline_mcp_settings.json` (or via the Cline settings UI).

StreamableHTTP (recommended for high throughput):

```json
{
  "mcpServers": {
    "DICOM Server": {
      "url": "http://localhost:8086/mcp?api_key=gs_live_YOUR_API_KEY_HERE",
      "type": "streamableHttp",
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

SSE variant:

```json
{
  "mcpServers": {
    "DICOM Server": {
      "url": "http://localhost:8086/sse?api_key=gs_live_YOUR_API_KEY_HERE",
      "type": "sse",
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

### AntiGravity

`~/.gemini/antigravity/mcp_config.json` (fallbacks: `~/.gemini/config/mcp_config.json`,
`~/.gemini/antigravity-cli/mcp/mcp_config.json`):

```json
{
  "mcpServers": {
    "gosmart-graphite": {
      "serverUrl": "http://127.0.0.1:8086/sse?api_key=gs_live_YOUR_API_KEY_HERE"
    }
  }
}
```

STDIO variant:

```json
{
  "mcpServers": {
    "gosmart-graphite": {
      "command": "uv",
      "args": ["run", "gosmart-graphite", "mcp", "--transport", "stdio"],
      "env": { "GOSMART_MCP_API_KEY": "gs_live_YOUR_API_KEY_HERE" }
    }
  }
}
```

## Next steps

- Browse the [Tool Catalog](tools.md) to see every capability.
- Try ready-made [Utterance Examples](utterance-examples.md).
