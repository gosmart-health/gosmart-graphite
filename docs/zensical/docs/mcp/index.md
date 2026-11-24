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

#### Option A: Via administrative CLI (out-of-band)

```bash
uv run python -m gosmart_graphite.cli.admin create-api-key \
    --email admin@gosmart.health \
    --name "Local Agent Key"

# With custom scopes and 90-day expiration
uv run python -m gosmart_graphite.cli.admin create-api-key \
    --email admin@gosmart.health \
    --name "AntiGravity Agent Key" \
    --scopes "mcp:full,graph:read" \
    --expires-days 90
```

#### Option B: Via REST API

1. Log in as an administrator to obtain an access token and user `id`:

   ```bash
   curl -X POST "http://localhost:8086/api/v1/auth/login" \
     -H "Content-Type: application/json" \
     -d '{"username": "admin", "password": "YourAdminPassword123!"}'
   ```

2. Generate the API key and save the returned `raw_api_key` (shown only once):

   ```bash
   curl -X POST "http://localhost:8086/api/v1/auth/api-keys" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer <ACCESS_TOKEN>" \
     -d '{
       "user_id": "<USER_ID>",
       "name": "Cursor Agent Key",
       "scopes": ["*"]
     }'
   ```

!!! important "Role Requirement for Mutating Tools"
    Configuration-changing tools (`create_*`, `update_*`, `delete_*`,
    `upsert_*`, assign/dissociate, `bootstrap_system_defaults`,
    `update_system_info`) require the caller to hold the **PLATFORM_ADMIN**
    role; otherwise the tool returns a permission error.

## Client setup

### Cursor

- User home directory: `~/.cursor/mcp.json` *(macOS/Linux: `$HOME/.cursor/mcp.json`, Windows: `%USERPROFILE%\.cursor\mcp.json`)*
- Or workspace-level: `.cursor/mcp.json`

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

- Global configuration: `~/.gemini/antigravity/mcp_config.json` *(macOS/Linux: `$HOME/.gemini/antigravity/mcp_config.json`, Windows: `%USERPROFILE%\.gemini\antigravity\mcp_config.json`)*
- Fallbacks: `~/.gemini/config/mcp_config.json`, `~/.gemini/antigravity-cli/mcp/mcp_config.json`

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
