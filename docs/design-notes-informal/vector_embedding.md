# Vector Embedding Notes

## Models

Clinical texts present unique challenges: dense jargon, abbreviations 
(e.g., "SOB" for shortness of breath), negations ("denies chest pain"),
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
clinical vocabulary, though constrained by BERT’s classic 512-token context window.

## Ollama - High-Capacity General Embeddings

qwen3-embedding (0.6B, 4B, 8B) (Ollama): Top-tier retrieval benchmarks with 
Matryoshka dimension scaling.

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

For example, `nomic-embed-text` model users `search_query:` prefix for search 
mode and `search_document: ` for encoding the target strings.

For example with the ``

### Normalize The Vectors

It is recommended to normalize the vectors to ensure consistent 
representation and improve the effectiveness of vector-based 
similarity search and retrieval tasks. See "Cosine Similarity" on the web,
but computation is more efficient as "dot product" can be used instead.

Index Performance: Vector databases (e.g., Chroma, Qdrant, Milvus, FAISS) 
run inner product (ip / dot product) searches significantly faster on 
normalized vectors than computing full Euclidean distance or unnormalized cosine angles at query time.

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

## GoSmart `VectorService` (Genkit Integration)

`VectorService` (`service/vector_service.py`) uses **Genkit** (`genkit`, `genkit-ollama`, and `genkit-google-genai`) to compute L2-normalized vector embeddings from either a local/network **Ollama** server or **Google Vertex AI**.

### Environment Configuration (`.env`)

```dotenv
# Provider: 'ollama' or 'vertexai'
GOSMART_GS_VECTOR_EMBED_PROVIDER=ollama
GOSMART_GS_VECTOR_EMBED_SERVER_URL=http://10.200.0.103:11434
GOSMART_GS_VECTOR_EMBED_MODEL=nomic-embed-text:latest
GOSMART_GS_VECTOR_EMBED_DIMENSIONS=768

# Optional for Vertex AI provider (supports API Key or ADC / Service Account):
# GOSMART_GS_VECTOR_EMBED_PROVIDER=vertexai
# GOSMART_GS_VECTOR_EMBED_MODEL=gemini-embedding-001
# GOSMART_GS_VECTOR_EMBED_DIMENSIONS=768
# GOSMART_GS_VERTEX_AI_API_KEY=your-vertex-ai-or-gemini-api-key
# GOSMART_GS_VERTEX_AI_LOCATION=us-central1
# GOOGLE_CLOUD_PROJECT=your-gcp-project-id
```

### Usage Example

```python
from service.vector_service import VectorService

vector_service = VectorService()
clinical_note = "Patient presents with acute onset dyspnea and denies chest pain."

# Synchronous call (also safe inside Jupyter notebooks)
normalized_vector = vector_service.embed_text(clinical_note)
print(f"Dimension: {len(normalized_vector)}")

# Asynchronous call
# normalized_vector = await vector_service.embed_text_async(clinical_note)
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

### Define the Index with `cosine`

```cypher
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("michiyasunaga/BioLinkBERT-base")
vector = model.encode(
    "Patient presents with acute onset dyspnea and denies chest pain.",
    normalize_embeddings=True,
)
```

### Searching with the cypher

```cypher
SEARCH clinical_text_embeddings, 5, $normalized_query_vector
YIELD node, score
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

Please continue to [Decoupled Vector Index Graph Pattern Design](./decoupled_vec_index_graph.md)

