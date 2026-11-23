# LLM Configurations

`LLM_Config` nodes store provider/model settings for the vector embedding and
generation services. They are persisted **dynamically** in Neo4j and scoped to
a sponsoring `(:Group)`, so different organizations on the same deployment can
use different models, endpoints, and credentials.

Providers are resolved through **Genkit** (`genkit-ollama`,
`genkit-google-genai`), supporting local Ollama models as well as Google
Vertex AI / Gemini.

## Registering a configuration

From a JSON file via the CLI:

```bash
uv run gosmart-graphite configure llm-config --file devop-scripts/llm_config.template.json
uv run gosmart-graphite configure llm-config --file /path/to/llm_config.json --assign "GoSmart Health"
```

- `--file` (`-f`) — path to a JSON file containing a single `LLM_Config`
  object (JSON arrays and scalar values are rejected).
- `--assign` (`-a`) — target `Group` ID, `name`, or `code`. When omitted,
  defaults to `'GoSmart Health'` (fails if that group does not exist or if
  multiple groups match).

Registering or overwriting a configuration **archives the prior version** via
`NOT_HAS_LLM_CONFIG` (soft-deletion semantics), preserving history.

## Template reference

The sanitized starter template (`devop-scripts/llm_config.template.json`):

```json
{
  "tag": "embedding-v1",
  "provider": "ollama",
  "model": "nomic-embed-text:latest",
  "url": "http://localhost:11434",
  "dimensions": 768,
  "dimensions_truncated": null,
  "api_key": "",
  "location": "us-central1",
  "project_id": null,
  "extra_config": null,
  "description": "A local development model"
}
```

| Field | Meaning |
|:------|:--------|
| `tag` | Purpose tag used to resolve the config (for example `embedding-v1`, `inference-v1`). |
| `provider` | Backend provider (`ollama`, `google`, ...). |
| `model` | Model name, e.g. `nomic-embed-text:latest` for Ollama embeddings. |
| `url` | Provider endpoint (Ollama host, ...). |
| `dimensions` | Embedding vector dimension produced by the model. |
| `dimensions_truncated` | Matryoshka truncation target, when supported. |
| `api_key` | Provider credential. **Never commit real keys.** |
| `location` | Cloud region (Google provider). |
| `project_id` | Cloud project ID (Google provider). |
| `extra_config` | Free-form provider-specific options. |
| `description` | Free-text description. |

## Key handling

- For cloud providers such as Vertex AI / Gemini, set `api_key` in your local
  (uncommitted) JSON file to an environment-variable reference such as
  `"${GOSMART_GS_VERTEX_AI_API_KEY}"`.
- To encrypt literal keys at rest in Neo4j, set
  `GOSMART_GS_LLM_CONFIG_ENCRYPTION_KEY` — see
  [Environment Variables](environment-variables.md#llm-configuration-security).
- Listing endpoints and MCP tools always return the `api_key` **masked**.
