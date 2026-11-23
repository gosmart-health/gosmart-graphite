# MCP Utterance Examples

Once an agent is [connected](index.md), you can operate GRAPHITE in natural
language. This page catalogues ready-made utterances and shows how the agent
resolves them to [MCP tools](tools.md).

> [!TIP]
> Be explicit with identifiers — patient IDs, Study Instance UIDs, AE titles,
> and config IDs produce far better tool calls than pronouns.

## Quick reference

| You say... | Tool invoked |
|:-----------|:-------------|
| "Is the server healthy?" | `health_check` |
| "Generate a mock CT study for patient PT-DEMO-001." | `generate_mock_dicom` |
| "Ingest the DICOM file at /data/head.dcm." | `ingest_dicom_file` |
| "Ping the PACS at 192.168.1.50:104." | `echo_scu` |
| "Send this study to the PACS." | `store_scu` |
| "List the first 10 patients." | `list_patients` |
| "Show me everything about patient PT-001." | `get_patient_graph` |
| "Undo that relationship deletion." | `restore_relationship` |
| "Run this Cypher query: ...". | `execute_cypher` |
| "What system is this?" | `get_system_info` |
| "Set up a new SCP for the CT scanner." | `create_dicom_configuration` *(admin)* |
| "Which embedding model is configured?" | `list_llm_configurations` |
| "Register an Ollama embedding model for Radiology." | `upsert_llm_configuration` *(admin)* |

## Health & ingestion

```text
"Is the server healthy?"
→ health_check()
```

```text
"Generate a mock CT study for patient PT-DEMO-001, patient name DOE^JANE,
 described as 'Head CT Scan'."
→ generate_mock_dicom(patient_id="PT-DEMO-001", patient_name="DOE^JANE",
                      modality="CT", study_description="Head CT Scan")
```

```text
"Ingest the DICOM file at /data/incoming/head-001.dcm."
→ ingest_dicom_file(file_path="/data/incoming/head-001.dcm", use_actor=True)
```

```text
"Can we reach the remote PACS? It listens on 192.168.1.50 port 104 as REMOTE_PACS."
→ echo_scu(host="192.168.1.50", port=104, called_aet="REMOTE_PACS")
```

```text
"Send every instance of study 1.2.840.99999999.34264206.1037082020670
 to REMOTE_PACS at 192.168.1.50:104, calling as GOSMART_GS."
→ store_scu(destination_aet="REMOTE_PACS", host="192.168.1.50", port=104,
            calling_aet="GOSMART_GS",
            study_instance_uid="1.2.840.99999999.34264206.1037082020670")
```

## Knowledge graph

```text
"List the first 10 patients with their study counts."
→ list_patients(limit=10)
```

```text
"Show me the full imaging graph for patient PT-DEMO-001."
→ get_patient_graph(patient_id="PT-DEMO-001")
```

```text
"How many Observation nodes mention anemia? Run:
 MATCH (obs:Observation) WHERE any(c IN obs.code WHERE c CONTAINS 'Anemia')
 RETURN count(obs) AS total"
→ execute_cypher(query="MATCH (obs:Observation) WHERE any(c IN obs.code
                        WHERE c CONTAINS 'Anemia') RETURN count(obs) AS total")
```

```text
"Soft-delete the HAS_STUDY link between that series and its study."
→ soft_delete_relationship(start_node_id=..., end_node_id=..., rel_type="HAS_STUDY")

"Actually, restore that link."
→ restore_relationship(start_node_id=..., end_node_id=..., rel_type="HAS_STUDY")
```

## System & DICOM configurations

```text
"Which system am I talking to, and which DICOM configurations does it have?"
→ get_system_info()
```

```text
"Set the system owner to 'Acme Health' and the contact email to
 imaging@acme.example."
→ update_system_info(owner="Acme Health", email="imaging@acme.example")   # admin
```

```text
"Register a new SCP: AE title CT_SCANNER listening on 0.0.0.0:11113,
 all services and transfer syntaxes, assigned to the GoSmart Health group."
→ create_dicom_configuration(ae_title="CT_SCANNER", roles_json='["SCP"]',
                             host="0.0.0.0", port=11113,
                             dicom_services_json='["*"]',
                             transfer_syntaxes_json='["*"]')                # admin
```

```text
"Move CT_SCANNER to port 11114."
→ update_dicom_configuration(config_id=..., port=11114)                    # admin

"Retire CT_SCANNER."
→ delete_dicom_configuration(config_id=...)                                # admin

"Bring CT_SCANNER back."
→ restore_dicom_configuration(config_id=...)                               # admin
```

## LLM configurations

```text
"Which embedding configurations exist for the Radiology group?"
→ list_llm_configurations(group_id="Radiology")
```

```text
"Register an Ollama embedding model nomic-embed-text:latest on
 http://localhost:11434 with 768 dimensions, tagged embedding-v1, and
 assign it to GoSmart Health."
→ upsert_llm_configuration(tag="embedding-v1", provider="ollama",
                           model="nomic-embed-text:latest",
                           url="http://localhost:11434", dimensions=768,
                           assign_group="GoSmart Health")                  # admin
```

```text
"Stop using that model for Radiology — but keep it recoverable."
→ dissociate_llm_configuration_from_group(config_id=..., group_id=...)     # admin

"Re-link it."
→ assign_llm_configuration_to_group(config_id=..., group="Radiology")      # admin
```

## Multi-step walkthroughs

### 1. Spin up a disposable test environment

```text
1. "Make sure the system is bootstrapped."
   → bootstrap_system_defaults()                                   # admin

2. "Create a mock MR study for TEST-001 with description 'Cervical Spine MR'."
   → generate_mock_dicom(patient_id="TEST-001", modality="MR",
                         study_description="Cervical Spine MR")

3. "Ingest /data/tests/spine-001.dcm through the actor pipeline."
   → ingest_dicom_file(file_path="/data/tests/spine-001.dcm", use_actor=True)

4. "List patients named TEST so I can verify."
   → execute_cypher(query="MATCH (p:Patient) WHERE p.patient_id STARTS WITH 'TEST'
                           RETURN p.patient_id, p.name LIMIT 20")
```

### 2. Route studies to a downstream PACS

```text
1. "Can we reach REMOTE_PACS at 10.0.0.20 port 104?"
   → echo_scu(host="10.0.0.20", port=104, called_aet="REMOTE_PACS")

2. "Send all instances of patient PT-DEMO-001's latest study to REMOTE_PACS."
   → store_scu(destination_aet="REMOTE_PACS", host="10.0.0.20", port=104,
               patient_id="PT-DEMO-001")

3. "Summarize what was sent."
   → get_patient_graph(patient_id="PT-DEMO-001")
```

The second call runs the query server-side: when no explicit instances are
given, `store_scu` resolves matching instances from the graph
(`patient_id` / `study_instance_uid` / `series_instance_uid` /
`sop_instance_uid`) and dispatches them through the Store SCU actor.

### 3. Find abnormal labs and pull the triggering imaging

```text
1. "Find erythrocyte-count observations above 15 and list the imaging
   studies performed in the same encounters:

   MATCH (obs:Observation)-[:PERFORMED_IN]->(e:Encounter)<-[:PERFORMED_IN]-(img:ImagingStudy)
   WHERE any(item IN obs.code WHERE item STARTS WITH 'http://loinc.org|788-0')
     AND any(item IN obs.numericValues WHERE item > 15.0)
   RETURN DISTINCT img.procedureCodeText, img.studyUid, img.seriesUid"

   → execute_cypher(query="...")

2. "Send the first of those studies to the research PACS RESEARCH_PACS
   at 192.168.1.60:104."
   → store_scu(destination_aet="RESEARCH_PACS", host="192.168.1.60", port=104,
               study_instance_uid="<studyUid from step 1>")
```

## Behavior notes

- **Test data stays hidden.** Tools default to `include_tests=false`, so
  records tagged with a `test_id` are excluded unless explicitly requested.
- **Admin operations fail loudly.** Without the PLATFORM_ADMIN role,
  configuration tools return a permission error — connect the agent with an
  API key minted for an administrator to manage configurations.
- **Deletions are recoverable.** `delete_*` tools soft-delete by default
  (`hard=true` to hard-delete); `restore_*` tools undo soft deletions.
- **Secrets are masked.** LLM configuration listings always redact `api_key`.
