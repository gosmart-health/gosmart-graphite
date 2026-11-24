# MCP Tool Catalog

The built-in MCP server (`src/gosmart_graphite/service/mcp_service.py`)
exposes the following tools. Configuration-changing tools require the caller
to hold the **PLATFORM_ADMIN** role (marked :material-shield-lock: **admin**).

## Health & Ingestion

| Tool | Purpose | Key parameters |
|:-----|:--------|:---------------|
| `health_check` | Check system health (Neo4j, Pykka actor, storage URI). | — |
| `generate_mock_dicom` | Generate a realistic mock DICOM file, store it locally, and index it into Neo4j. | `patient_id`, `patient_name`, `modality`, `study_description` |
| `ingest_dicom_file` | Parse and ingest a local DICOM file into the graph and storage root. | `file_path`, `use_actor` |
| `echo_scu` | Test network connectivity to a DICOM SCP using C-ECHO. | `host`, `port`, `calling_aet`, `called_aet` |
| `store_scu` | Send DICOM instance(s) or query-matched instances to a remote AE via the Store SCU actor. | `destination_aet`, optional `study/series/sop` UIDs or `patient_id` to auto-match, `host`, `port`, `calling_aet`, `timeout` |

## Knowledge Graph

| Tool | Purpose | Key parameters |
|:-----|:--------|:---------------|
| `list_patients` | List patients and their study counts. | `limit`, `include_tests`, `test_id` |
| `get_patient_graph` | Retrieve the full hierarchy `Patient → Studies → Series → Instances` for a patient. | `patient_id`, `include_tests` |
| `soft_delete_relationship` | Atomically soft-delete a relationship (clones properties to `NOT_<rel_type>`). | `start_node_id`, `end_node_id`, `rel_type` |
| `restore_relationship` | Atomically restore a soft-deleted relationship back to `<rel_type>`. | `start_node_id`, `end_node_id`, `rel_type` |
| `execute_cypher` | Execute a custom Cypher query against the graph database. | `query`, `parameters_json` |

## System & DICOM Configurations

| Tool | Purpose | Key parameters |
|:-----|:--------|:---------------|
| `get_system_info` | Retrieve root System properties, ownership info, and linked configurations. | `include_configs` |
| `update_system_info` :material-shield-lock: **admin** | Update mutable System properties (installer configuration). | `name`, `owner`, `address`, `email`, `phone`, `serial_number` |
| `bootstrap_system_defaults` :material-shield-lock: **admin** | Ensure the root System node and default promiscuous DICOM configuration exist. | — |
| `list_dicom_configurations` | List active DICOM configurations. | `role`, `is_promiscuous`, `include_tests` |
| `get_dicom_configuration` | Retrieve a single DICOM configuration by its deterministic ID. | `config_id` |
| `create_dicom_configuration` :material-shield-lock: **admin** | Create a new configuration attached to a sponsoring Group (or default `'GoSmart Health'`). | `ae_title`, `roles_json`, `host`, `port`, `tls_enabled`, `dicom_services_json`, `transfer_syntaxes_json`, `storage_root_uri`, `group_id` |
| `update_dicom_configuration` :material-shield-lock: **admin** | Update mutable properties of an existing configuration. | `config_id`, `ae_title`, `roles_json`, `host`, `port`, ... |
| `delete_dicom_configuration` :material-shield-lock: **admin** | Soft-delete (or hard-delete) a configuration; SCP drains gracefully. | `config_id`, `hard` |
| `restore_dicom_configuration` :material-shield-lock: **admin** | Restore a previously soft-deleted configuration and reactivate its SCP. | `config_id` |

## LLM Configurations

| Tool | Purpose | Key parameters |
|:-----|:--------|:---------------|
| `list_llm_configurations` | List active group-scoped LLM configs (`api_key` redacted). | `tag`, `provider`, `group_id`, `include_tests` |
| `get_llm_configuration` | Retrieve a single active config by ID or by `(tag[, group_id])` (`api_key` redacted). | `config_id`, `tag`, `group_id` |
| `upsert_llm_configuration` :material-shield-lock: **admin** | Register or overwrite a purpose-tagged config (archives prior version) and assign it to a Group. | `tag`, `provider`, `model`, `url`, `dimensions`, `api_key`, `assign_group` |
| `assign_llm_configuration_to_group` :material-shield-lock: **admin** | Assign an existing active config to a target Group via `HAS_LLM_CONFIG`. | `config_id`, `group` |
| `dissociate_llm_configuration_from_group` :material-shield-lock: **admin** | Soft-dissociate a Group (`HAS_LLM_CONFIG` → `NOT_HAS_LLM_CONFIG`). | `config_id`, `group_id` |
| `delete_llm_configuration` :material-shield-lock: **admin** | Soft-delete (or hard-delete) a config node. | `config_id`, `hard` |
| `restore_llm_configuration` :material-shield-lock: **admin** | Restore a previously soft-deleted config node. | `config_id` |

!!! note
    Fields suffixed `_json` accept JSON-encoded arrays/objects (for example
    `roles_json='["SCP"]'`) because MCP tool parameters are flat strings.
    `test_id`-prefixed parameters scope operations to test data and keep
    production records untouched.

See [Utterance Examples](utterance-examples.md) for how to phrase these as
natural-language requests.
