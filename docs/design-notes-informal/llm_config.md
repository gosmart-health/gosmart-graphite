# Refactoring the LLM Configurations

## Reason for Changes

We will be using different LLM configurations for different purposes and also 
configurations may be different for organizations which call them, for example, 
for subscription payment account differences.

## Changes

### Current Usage

There is a single set of parameters as below staring with VECTOR_ and VERTEX_
environment variables. This only supports a system specific configuration.

```
 # Vector Embedding Settings (Genkit: Ollama or Google Vertex AI)
    VECTOR_EMBED_PROVIDER: str = Field(
        default="",
        description="Vector embedding provider: 'ollama' or 'vertexai' (empty disables vector embedding)",
    )
    VECTOR_EMBED_MODEL: str = Field(
        default="",
        description="Vector embedding model name (e.g. 'nomic-embed-text:latest' or 'gemini-embedding-001')",
    )
    VECTOR_EMBED_SERVER_URL: str = Field(
        default="",
        description="Base URL for the local/network Ollama server (e.g. 'http://10.200.0.103:11434')",
    )
    VECTOR_EMBED_DIMENSIONS: Optional[int] = Field(
        default=None,
        description="Expected or target output vector dimensionality (e.g. 768 for nomic-embed-text:latest)",
    )
    VERTEX_AI_API_KEY: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices(
            "VERTEX_AI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "GOSMART_GS_VERTEX_AI_API_KEY",
        ),
        description="API key for Google Vertex AI (Express Mode) / Gemini API authentication",
    )
    VERTEX_AI_LOCATION: str = Field(
        default="us-central1",
        validation_alias=AliasChoices(
            "VERTEX_AI_LOCATION",
            "GOOGLE_CLOUD_LOCATION",
            "GOSMART_GS_VERTEX_AI_LOCATION",
        ),
        description="Google Cloud region for Vertex AI embeddings (default: us-central1)",
    )
```

### Proposed Changes

#### Create a class `LLM_Config` from Auditable class

The `LLM_Config` is associated with the `LLM_Config` nodes in neo4j database.
Each node is assignable to one or more `Group` node which is the "Sponsoring"
organization for the LLM API. The relationship shall be ':CONFIGURES'.

- The following will be the members
- tag: The str tag (key) of the configuration.
- provider: str name of the provider 'ollama' or 'vertexai' etc.
- model: str of the canonical model name to be applied
- url: The location of the API
- dimensions: number - embedding dimensions
- dimensions_truncated: Optional truncated dimensions
- api_key: str: API key, default should be an empty string.
- location: str: canonical cloud service location string (us-central1 etc)

#### Create a CLI that will register a JSON file of the above configuration

The CLI shall have the following command form

configure llm-config --file <llm_config.json> --assign <name or id>

- if the `--assign` option is not provided, the configuration shall be assigned 
  to the default group which is 
- The default group has the `name` property of 'GoSmart Health'.

The default group node is found with:

```
MATCH (n: Group)
WHERE n.name = 'GoSmart Health'
RETURN n
```

If --assign turns up more than one matching nodes, that is a runtime error.
Repeated call to this CLI command will over-write the existing configuration.

> Current Limitation
> We do not yet have crafted well functioning Group nodes. For now use the default group.
> The intention is to have a current active Group context and from there we will
> traverse up the group tree to find the matching configuration containing the 
> `tag` to construct the LLM client or call the LLM.
> 

In the `devop-scripts` provide configure-llm.sh script with a template JSON 
file.



