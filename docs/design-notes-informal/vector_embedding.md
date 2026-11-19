# Vector Embedding Notes

## Terminology

In category theory, an embedding is a specific type of functor that places one
category inside another without losing its structural identity.

**Vector Embedding** Refers to the method or process of mapping discrete
objects.

**Embeddings** (With plural) Usually refer to the derived vectors in our case.

## Models

Clinical texts present unique challenges: dense jargon, abbreviations (e.g.,
"SOB" for shortness of breath), negations ("denies chest pain"),
and varied context lengths (EHR progress notes vs. discharge summaries).

## Suggested Models

### Hugging Face - Specialized Biomedical & Clinical Models

For clinical-specific terminology, running specialized encoders via Hugging Face
(sentence-transformers or transformers) generally outperforms generic
baselines:

Simonlee711/Clinical_ModernBERT: Built on the ModernBERT architecture,
it supports an 8,192-token context window (crucial for long clinical
records and discharge summaries).

michiyasunaga/BioLinkBERT-base: Pretrained on PubMed and biomedical
literature with document link/citation graph awareness; strong
performance on biomedical benchmarks.

emilyalsentzer/Bio_ClinicalBERT: Trained on MIMIC-III EHR notes. Excellent for
clinical vocabulary, though constrained by BERT’s classic 512-token context
window.

## Ollama - High-Capacity General Embeddings

embeddinggemma-2:270m: (Ollama) Provided by Google, it can support either basic
text, or audio and
video. We only need text so we will use 270m Text model, there is a 570m text
model as well. We may recommend it for production.

qwen3-embedding (0.6B, 4B, 8B) (Ollama): Top-tier retrieval benchmarks with
Matryoshka dimension scaling.

### No Longer Favored

The `nomic` models are still fine in 2026, however in 2025 `nomic.ai` pivoted
its core business to Construction AI and we anticipate the development
of this will taper off.

### Native Hidden Sizes

- 0.6B: 1024
- 4B: 2560 (Matryoshka allows truncation to 1024)

[LINK](https://ollama.com/library/qwen3-embedding)

bge-m3 (Ollama): Supports 8,192 tokens, dense/sparse/multi-vector retrieval,
and handles multilingual terminology. [LINK](https://ollama.ai/bge-m3)

nomic-embed-text (Ollama): Lightweight (137M parameters), 8,192 token limit,
and fast for local development. [LINK](https://ollama.ai/nomic-embed-text)

## Vector Usage

### Do not Mix and Match Models Even the Parameter Sizes

Even under the same model name like `qwen3-embedding`, it is important to use
consistent models for vector embeddings to ensure accurate and meaningful
similarity comparisons. Mixing models and parameter size version can lead to
inconsistent results and
may negatively impact the effectiveness of vector-based similarity search and
retrieval tasks.

### Be aware of Asymmetric Embedding - Feed Texts with Prefixes

When feeding texts to vector embedding models, be aware that it is recommended
to use
appropriate instruction prefixes based on the model and mode ('search' or
'document'). This helps ensure that the model interprets the input text
correctly and produces meaningful embeddings. See "Instruction Prefixes" for
more details.

See [Embedding Gemma 2 - The Developer 
Guide](https://developers.googleblog.com/embeddinggemma-2-the-developer-guide/)

We will be using:

- **Document Embeddings**: `title: none | <the text>`
- **Search Queries**: `task: search result| query`

### Normalize The Vectors

It is recommended to normalize the vectors to ensure consistent
representation and improve the effectiveness of vector-based
similarity search and retrieval tasks. See "Cosine Similarity" on the web,
but computation is more efficient as "dot product" can be used instead.

Index Performance: Vector databases (e.g., Chroma, Qdrant, Milvus, FAISS)
run inner product (ip / dot product) searches significantly faster on
normalized vectors than computing full Euclidean distance or unnormalized cosine
angles at query time.

Length Bias Mitigation: Clinical notes vary widely in length. Normalization
prevents vector magnitude (which often correlates with text token length)
from distorting semantic relevance.

#### Python Normalization Example with NumPy

```python
# L2 normalization using NumPy
norm = np.linalg.norm(vector)
if norm > 0:
    normalized_vector = vector / norm
```

## GoSmart `EmbeddingService` / `VectorService` (Dynamic `LLM_Config` + Genkit)

`EmbeddingService` (`src/gosmart_graphite/service/embedding_service.py`, aliased
as `VectorService`) uses **Genkit** (`genkit`, `genkit-ollama`, and
`genkit-google-genai`) to compute L2-normalized vector embeddings from either a
local/network **Ollama** server or **Google Vertex AI / Gemini API**.

Runtime parameters are **not** read from static `VECTOR_*` / `VERTEX_*`
environment variables. Instead, `EmbeddingService` dynamically resolves the
active `(:LLM_Config {tag: "embedding"})` node in Neo4j for the active
`(:Group)` (traversing upward via `(:Group)-[:BELONGS_TO*0..]->(:Group)` with
`'GoSmart Health'` fallback), caches Genkit clients per configuration
fingerprint, applies optional Matryoshka truncation
(`dimensions_truncated <= dimensions`), and performs L2 normalization.

### Registering an Embedding `LLM_Config` in Neo4j

Create a local JSON file (for example starting from
`devop-scripts/llm_config.template.json`):

```json
{
  "tag": "embedding",
  "provider": "ollama",
  "model": "nomic-embed-text:latest",
  "url": "http://localhost:11434",
  "dimensions": 768,
  "dimensions_truncated": null,
  "api_key": "",
  "location": "us-central1"
}
```

Register or overwrite it in Neo4j via CLI:

```bash
uv run gosmart-graphite configure llm-config --file devop-scripts/llm_config.template.json --assign "GoSmart Health"
```

For cloud providers, `api_key` may store an environment variable reference such
as `"${GOSMART_GS_VERTEX_AI_API_KEY}"`, or be encrypted at rest
(`enc:fernet:...`) when `GOSMART_GS_LLM_CONFIG_ENCRYPTION_KEY` is configured.

### Usage Example

```python
from gosmart_graphite.service.embedding_service import EmbeddingService

embedding_service = EmbeddingService()
clinical_note = "Patient presents with acute onset dyspnea and denies chest pain."

# Synchronous call (resolves active Group from ContextVar or 'GoSmart Health')
normalized_vector = embedding_service.embed_text(clinical_note)
print(f"Dimension: {len(normalized_vector)}")

# Scoped to a specific Group or custom purpose tag
# normalized_vector = await embedding_service.embed_text_async(
#     clinical_note,
#     group_id="<group-id>",
#     tag="embedding",
# )
```

## Practical Python Example Code

### Pulling in the Model

`ollama pull nomic-embed-text`

### Ollama Embedding

```python
import numpy as np
import ollama

clinical_note = "Patient presents with acute onset dyspnea and denies chest pain."

# 1. Compute embedding using Ollama
response = ollama.embed(model="bge-m3", input=clinical_note)

# response["embeddings"] returns a list of vectors (one per input string)
vector = np.array(response["embeddings"][0], dtype=np.float32)

print(f"Embedding dimension: {vector.shape[0]}")
```

### Hugging Face Exclusive Weights

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("michiyasunaga/BioLinkBERT-base")
vector = model.encode(
    "Patient presents with acute onset dyspnea and denies chest pain.",
    normalize_embeddings=True,
)
```

## Cypher Examples

### Add a Vector in Node or Relation

It is just a matter of adding a floating point array to a node or relation
property.

```python
# 1. Generate embedding locally
text = f"{movie['title']}: {movie['plot']}"
embedding_vector = local_model.encode(text).tolist()  # list of 1536 floats

# 2. Save it directly to the node
query = """
MATCH (m:Movie {id: $id})
SET m.embedding = $embedding
"""

session.run(query, id=movie["id"], embedding=embedding_vector)
```

### Define the Index with `cosine`

#### Similarity Functions Supported

`cosine` or `euclidian` can be assigned to `vector.similarity_function` option.

```
CREATE VECTOR INDEX moviePlots IF NOT EXISTS
FOR (m:Movie)
ON m.embedding
OPTIONS { indexConfig: {
`vector.dimensions`: 1536,
`vector.similarity_function`: 'cosine'
}}

```

Another example here from the Neo4j Documentation is worth a understanding.

**What This Means**

For every Movie and Actor node that has an already-existing 1536-dimensional
vector stored in n.embedding, create a vector search index using cosine
similarity.
Along with each indexed vector, store copies of title, plot, name, and born (if
they exist on the node) so they can be returned quickly alongside search
results without extra database lookups

``` cypher
CREATE VECTOR INDEX multiLabelAdditionalProperties IF NOT EXISTS
FOR (n:Movie | Actor) ON n.embedding
WITH [n.title, n.plot, n.name, n.born]
OPTIONS {indexConfig: {`vector.dimensions`: 1536,
`vector.similarity_function`: 'cosine'}}

```

### Searching with the cypher

```cypher
SEARCH clinical_text_embeddings, 5, $normalized_query_vector YIELD node, score
RETURN node.id, node.text, score

```

It sets the maximum number of nearest neighbor nodes the HNSW (Hierarchical
Navigable Small World) index returns.

If your database has 5 or more indexed nodes, the index yields the top 5
closest matches ordered by descending similarity.

If your graph has fewer matching nodes (e.g., only 3 nodes exist in the index),
it returns as many as exist (up to 5).

The meaning of the score:

* 1.0: The most similar node to the query vector.
* 0.0: The least similar node to the query vector.
* -1.0: The node has the "exactly opposite" similarity to the query vector.

## Schema Design Pattern

Please continue
to [Decoupled Vector Index Graph Pattern Design](./decoupled_vec_index_graph.md)
