# Bootstrapping & Administration

This page covers superuser bootstrapping, out-of-band disaster recovery, API key
management, and system-level administrative endpoints.

!!! danger "Server Platform Access Control"
    The security model assumes that the host server platform, container
    execution environment, filesystem, environment variable configurations, and
    direct Neo4j database ports are **strictly access-controlled and hardened**.

    Direct shell access to the host/container or write access to environment
    variables enables out-of-band administrative privileges (such as password
    resets via CLI or `GOSMART_GS_FORCE_ADMIN_PASSWORD_RESET`). Restrict SSH,
    container `exec`, and configuration management access to authorized
    infrastructure administrators only.

## 1. Automated Superuser Bootstrapping

On startup, GRAPHITE checks whether a Platform Administrator (`PLATFORM_ADMIN`)
exists in Neo4j. If none exists and `GOSMART_GS_INITIAL_ADMIN_PASSWORD` is set,
the server creates the superuser account and affiliates it with the
`'GoSmart Health'` Catch-All group:

```bash
# In .env or deployment environment:
GOSMART_GS_INITIAL_ADMIN_EMAIL=admin@gosmart.health
GOSMART_GS_INITIAL_ADMIN_PASSWORD=MySecurePassword123!
GOSMART_GS_INITIAL_ADMIN_USERNAME=admin
```

!!! note "Idempotent Bootstrapping"
    Bootstrapping is **idempotent**. On subsequent restarts, existing admin
    credentials and database state are preserved unless
    `GOSMART_GS_FORCE_ADMIN_PASSWORD_RESET=true` is explicitly enabled.

See [Environment Variables](environment-variables.md#admin-bootstrapping) for
all bootstrap variables (`GOSMART_GS_INITIAL_ADMIN_*`).

## 2. Out-of-Band Disaster Recovery & Password Reset

If an administrator loses access, two recovery mechanisms are available.

### Mechanism A: Administrative CLI Utility (Host / Container)

Run [`gosmart_graphite.cli.admin`](https://github.com/gosmart-health/gosmart-graphite/blob/main/src/gosmart_graphite/cli/admin.py)
directly on the host or inside the container:

```bash
# Reset an existing administrator's password
uv run python -m gosmart_graphite.cli.admin reset-password \
    --email admin@gosmart.health \
    --password "NewEmergencyPassword123!"

# Or omit --password to enter it securely via prompt
uv run python -m gosmart_graphite.cli.admin reset-password --email admin@gosmart.health

# Create a new Platform Administrator
uv run python -m gosmart_graphite.cli.admin create-admin \
    --username emergency_admin \
    --email emergency@gosmart.health

# List all Platform Administrators and their status
uv run python -m gosmart_graphite.cli.admin list-admins
```

### Mechanism B: Startup Force-Reset Flag (Docker / Kubernetes)

For containerized deployments where interactive CLI access is inconvenient:

1. Update environment variables in your deployment configuration:

   ```bash
   GOSMART_GS_INITIAL_ADMIN_EMAIL=admin@gosmart.health
   GOSMART_GS_INITIAL_ADMIN_PASSWORD=NewEmergencyPassword123!
   GOSMART_GS_FORCE_ADMIN_PASSWORD_RESET=true
   ```

2. Restart the container. On startup, the server updates the admin password
   hash and records a security audit log entry.
3. Log in with the new password and remove
   `GOSMART_GS_FORCE_ADMIN_PASSWORD_RESET=true` from your environment.

## 3. Out-of-Band API Key Generation

Generate scoped API keys directly via the administrative CLI for MCP agents,
automation scripts, and external integrations:

```bash
# Generate an API key for an administrator or user
uv run python -m gosmart_graphite.cli.admin create-api-key \
    --email admin@gosmart.health \
    --name "Cursor Agent Key"

# With custom scopes and 90-day expiration
uv run python -m gosmart_graphite.cli.admin create-api-key \
    --email admin@gosmart.health \
    --name "AntiGravity Agent Key" \
    --scopes "mcp:full,graph:read" \
    --expires-days 90
```

Example output:

```text
Successfully generated API key:
-----------------------------------------------------------------
  User ID:      eabebc72-5c2d-5b74-abfe-78aceb55a5fa
  User Email:   admin@gosmart.health
  Key Name:     Cursor Agent Key
  Scopes:       ['*']
  Expires At:   Never
  API Key:      gs_live_0123456789abcdef0123456789abcdef...
-----------------------------------------------------------------
```

For obtaining API keys over the REST API and connecting AI agents, see
[MCP Overview — Authentication](../mcp/index.md#authentication).

## 4. System & Maintenance REST Endpoints

Protected endpoints require either an API key (`X-API-Key: gs_live_...` or
`Authorization: Bearer gs_live_...`) or a JWT access token
(`Authorization: Bearer <access_token>`). See `/docs` (Swagger UI) for full
request/response schemas.

| Method & Endpoint | Description |
|:------------------|:------------|
| `GET /api/v1/system` | Inspect root `System` metadata (`name`, `owner`, `address`, `email`, `phone`, `serial_number`, `installed_at`) and attached configurations. |
| `PUT /api/v1/system` | Update mutable `System` properties. |
| `POST /api/v1/system/bootstrap` | Idempotent environment bootstrap (ensures root `(:System)`, default groups, and default promiscuous DICOM entry exist). |
| `POST /api/v1/system/test/cleanup` | Detach and delete test nodes across target labels where `test_id` is defined and non-empty; drains associated ephemeral test SCP instances. |
