# DICOM Configurations

`DICOMConfiguration` nodes define how GRAPHITE participates in the DICOM
network: which Application Entity Titles it answers as (SCP), which remote AEs
it can push to (SCU), on which host/port, and with which service classes and
transfer syntaxes.

## Registering a configuration

From a JSON file via the CLI:

```bash
uv run gosmart-graphite configure dicom-config --file devop-scripts/dicom_config.template.json
uv run gosmart-graphite configure dicom-config --file /path/to/my_dicom_config.json --assign <group_id>
```

Or directly over REST / MCP — see the [Tool Catalog](../mcp/tools.md).

## Template reference

The sanitized starter template (`devop-scripts/dicom_config.template.json`):

```json
{
  "ae_title": "GOSMART_GS",
  "roles": ["SCP"],
  "host": "0.0.0.0",
  "port": 11112,
  "is_local": true,
  "tls_enabled": false,
  "tls_cert_path": null,
  "tls_key_path": null,
  "tls_ca_path": null,
  "tls_ciphers": null,
  "dicom_services": ["*"],
  "transfer_syntaxes": ["*"],
  "dicomweb_enabled": true,
  "dicomweb_url_prefix": "/dicomweb",
  "dicomweb_qido_enabled": true,
  "dicomweb_wado_enabled": true,
  "dicomweb_stow_enabled": true,
  "description": "Local development DICOM SCP configuration",
  "storage_root_uri": ""
}
```

| Field | Meaning |
|:------|:--------|
| `ae_title` | Application Entity Title. `*` acts as a promiscuous entry accepting any called AE title. |
| `roles` | Network roles for this configuration, e.g. `SCP` and/or `SCU`. An SCP is launched dynamically when a role includes `SCP`. |
| `host`, `port` | Bind address and port for the SCP listener. |
| `is_local` | Marks the AE as local to this deployment. |
| `tls_enabled`, `tls_cert_path`, `tls_key_path`, `tls_ca_path`, `tls_ciphers` | Optional TLS settings for encrypted associations. |
| `dicom_services` | Allowed SOP service classes during presentation-context negotiation (`*` = all). |
| `transfer_syntaxes` | Allowed transfer syntaxes during negotiation (`*` = all). |
| `dicomweb_*` | DICOMweb service switches and the URL prefix for `STOW-RS` / `WADO-RS` / `QIDO-RS`. |
| `storage_root_uri` | Optional per-configuration storage root override. |
| `description` | Free-text description shown in listings. |

## Dynamic SCP lifecycle

Configurations drive SCP instances at runtime:

- **Added** — a new SCP instance is instantiated and launched immediately on
  its configured host, port, and AE title.
- **Changed** — existing association threads continue uninterrupted while a
  new SCP instance launches with the updated configuration.
- **Removed** — the SCP transitions to a *draining* state: it stops accepting
  new associations while letting active transfer threads complete.

Service class and transfer-syntax enforcement applies during every association
negotiation: unconfigured services or syntaxes are rejected.

## Deletion & restoration

`DELETE /api/v1/configurations/dicom/{config_id}` soft-deletes the node
(`?hard=true` for a hard delete) and drains in-flight associations gracefully;
`POST .../restore` reactivates it. Equivalent MCP tools exist — see
[Tool Catalog](../mcp/tools.md) and the general
[`NOT_` semantics](index.md#immutability-soft-deletion-not_-semantics).
