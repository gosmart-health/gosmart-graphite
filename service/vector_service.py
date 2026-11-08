"""Vector Embedding Computation Service using Genkit (Ollama, Google AI, and Google Vertex AI)."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Any, Literal, Optional, Sequence, Union

import numpy as np
from genkit import Genkit
from genkit_google_genai import GoogleAI, VertexAI
from genkit_ollama import Ollama

from core.config import Settings, get_settings
from core.logging import get_logger

logger = get_logger("gosmart_gs.vector_service")


class SimilarityMetric(StrEnum):
    """Supported vector similarity and distance metrics mapped to a [0.0, 1.0] score."""

    COSINE = "cosine"
    DOT_PRODUCT = "dot_product"
    EUCLIDEAN = "euclidean"
    MANHATTAN = "manhattan"


SimilarityMetricType = Union[
    SimilarityMetric,
    Literal["cosine", "dot_product", "cosine_dot_product", "dot", "euclidean", "l2", "manhattan", "l1"],
]


def _prepare_comparison_arrays(
    query_vector: Sequence[float],
    target_vectors: Sequence[Sequence[float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Validate and convert query_vector (1D) and target_vectors (2D) to float32 NumPy arrays."""
    q = np.asarray(query_vector, dtype=np.float32)
    if q.ndim != 1:
        q = q.ravel()
    if q.size == 0:
        raise ValueError("query_vector must not be empty.")

    if len(target_vectors) == 0:
        return q, np.empty((0, q.size), dtype=np.float32)

    targets = np.asarray(target_vectors, dtype=np.float32)
    if targets.ndim == 1:
        targets = targets.reshape(1, -1)
    elif targets.ndim != 2:
        raise ValueError("target_vectors must be a 2D sequence of vectors.")

    if targets.shape[1] != q.size:
        raise ValueError(
            f"Vector dimension mismatch: query_vector has length {q.size}, "
            f"but target_vectors have dimension {targets.shape[1]}."
        )
    return q, targets


def compute_cosine_scores(
    query_vector: Sequence[float],
    target_vectors: Sequence[Sequence[float]],
) -> list[float]:
    """Compute cosine similarity against a list of target vectors, mapped to [0.0, 1.0] in index order.

    Follows Neo4j HNSW cosine similarity score convention: ``(cos_theta + 1.0) / 2.0``,
    where 1.0 is identical, 0.5 is orthogonal, and 0.0 is opposite.
    """
    q, targets = _prepare_comparison_arrays(query_vector, target_vectors)
    if targets.shape[0] == 0:
        return []

    q_norm = float(np.linalg.norm(q))
    t_norms = np.linalg.norm(targets, axis=1)
    denom = q_norm * t_norms

    dots = targets @ q
    cos_sim = np.where(denom > 0.0, dots / np.where(denom > 0.0, denom, 1.0), -1.0)
    cos_sim = np.clip(cos_sim, -1.0, 1.0)
    scores = np.clip((cos_sim + 1.0) / 2.0, 0.0, 1.0)
    return [float(s) for s in scores]


def compute_dot_product_scores(
    query_vector: Sequence[float],
    target_vectors: Sequence[Sequence[float]],
) -> list[float]:
    """Compute cosine dot-product similarity (assuming L2-normalized vectors) mapped to [0.0, 1.0].

    Uses ``(q . t + 1.0) / 2.0`` clamped to ``[0.0, 1.0]``, avoiding per-vector norm calculation.
    """
    q, targets = _prepare_comparison_arrays(query_vector, target_vectors)
    if targets.shape[0] == 0:
        return []

    dots = np.clip(targets @ q, -1.0, 1.0)
    scores = np.clip((dots + 1.0) / 2.0, 0.0, 1.0)
    return [float(s) for s in scores]


def compute_euclidean_scores(
    query_vector: Sequence[float],
    target_vectors: Sequence[Sequence[float]],
) -> list[float]:
    """Compute Euclidean (L2) similarity scores in [0.0, 1.0] in index order.

    Uses ``1.0 / (1.0 + l2_distance)``, where distance 0.0 yields 1.0 and larger distances
    monotonically approach 0.0.
    """
    q, targets = _prepare_comparison_arrays(query_vector, target_vectors)
    if targets.shape[0] == 0:
        return []

    dists = np.linalg.norm(targets - q, axis=1)
    scores = 1.0 / (1.0 + dists)
    return [float(s) for s in np.clip(scores, 0.0, 1.0)]


def compute_manhattan_scores(
    query_vector: Sequence[float],
    target_vectors: Sequence[Sequence[float]],
) -> list[float]:
    """Compute Manhattan (L1) similarity scores in [0.0, 1.0] in index order.

    Uses ``1.0 / (1.0 + l1_distance)``, where distance 0.0 yields 1.0 and larger distances
    monotonically approach 0.0.
    """
    q, targets = _prepare_comparison_arrays(query_vector, target_vectors)
    if targets.shape[0] == 0:
        return []

    dists = np.sum(np.abs(targets - q), axis=1)
    scores = 1.0 / (1.0 + dists)
    return [float(s) for s in np.clip(scores, 0.0, 1.0)]


def compute_similarity_scores(
    query_vector: Sequence[float],
    target_vectors: Sequence[Sequence[float]],
    metric: SimilarityMetricType = SimilarityMetric.COSINE,
) -> list[float]:
    """Compute similarity scores in [0.0, 1.0] between query_vector and target_vectors in index order."""
    m = str(metric).strip().lower()
    if m == "cosine":
        return compute_cosine_scores(query_vector, target_vectors)
    if m in ("dot_product", "cosine_dot_product", "dot"):
        return compute_dot_product_scores(query_vector, target_vectors)
    if m in ("euclidean", "l2"):
        return compute_euclidean_scores(query_vector, target_vectors)
    if m in ("manhattan", "l1"):
        return compute_manhattan_scores(query_vector, target_vectors)
    raise ValueError(
        f"Unsupported similarity metric '{metric}'. "
        "Expected 'cosine', 'dot_product' ('cosine_dot_product'), 'euclidean', or 'manhattan'."
    )


def normalize_vector(vector: Sequence[float], dimensions: Optional[int] = None) -> list[float]:
    """L2-normalize a vector using NumPy and return it as a list of Python floats.

    If ``dimensions`` is provided and positive, the vector is truncated (Matryoshka
    dimension scaling) or validated against the expected size before normalization.
    """
    arr = np.asarray(vector, dtype=np.float32)
    if arr.ndim != 1:
        arr = arr.ravel()
    if arr.size == 0:
        raise ValueError("Cannot normalize an empty vector.")

    if dimensions is not None and dimensions > 0:
        if arr.size < dimensions:
            raise ValueError(
                f"Embedding vector length ({arr.size}) is smaller than configured dimensions ({dimensions})."
            )
        if arr.size > dimensions:
            arr = arr[:dimensions]

    norm = float(np.linalg.norm(arr))
    if norm > 0.0:
        arr = arr / norm

    return arr.tolist()


class VectorService:
    """Computes L2-normalized text embeddings via Genkit (Ollama, Google AI / Gemini, or Vertex AI)."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        ai: Optional[Genkit] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._ai: Optional[Genkit] = ai

    def _get_api_key(self) -> Optional[str]:
        """Return the configured Google / Vertex AI / Gemini API key if non-empty."""
        if self.settings.VERTEX_AI_API_KEY is not None:
            val = self.settings.VERTEX_AI_API_KEY.get_secret_value().strip()
            if val:
                return val
        return None

    def is_configured(self) -> bool:
        """Return True if vector embedding model and required provider settings are configured."""
        model = (self.settings.VECTOR_EMBED_MODEL or "").strip()
        if not model:
            return False

        provider = self.resolve_provider()
        if provider == "ollama":
            return bool((self.settings.VECTOR_EMBED_SERVER_URL or "").strip())
        if provider in ("vertexai", "googleai"):
            return True
        return False

    def resolve_provider(self) -> str:
        """Determine the active provider ('ollama', 'googleai', or 'vertexai') from settings."""
        provider = (self.settings.VECTOR_EMBED_PROVIDER or "").strip().lower()
        model = (self.settings.VECTOR_EMBED_MODEL or "").strip()

        if model.startswith("ollama/"):
            return "ollama"
        if model.startswith("googleai/"):
            return "googleai"
        if model.startswith("vertexai/"):
            if self._get_api_key() and not self.settings.GOOGLE_CLOUD_PROJECT:
                return "googleai"
            return "vertexai"

        if provider == "ollama":
            return "ollama"
        if provider in ("googleai", "google_ai", "gemini"):
            return "googleai"
        if provider in ("vertexai", "vertex", "vertex_ai", "google", "google_vertex"):
            if self._get_api_key() and not self.settings.GOOGLE_CLOUD_PROJECT:
                return "googleai"
            return "vertexai"

        if (self.settings.VECTOR_EMBED_SERVER_URL or "").strip():
            return "ollama"

        return provider

    def resolve_embedder_name(self) -> str:
        """Return the fully-qualified Genkit embedder identifier (e.g. 'ollama/nomic-embed-text:latest')."""
        model = (self.settings.VECTOR_EMBED_MODEL or "").strip()
        if not model:
            raise ValueError("GOSMART_GS_VECTOR_EMBED_MODEL is not configured.")

        for prefix in ("ollama/", "googleai/", "vertexai/"):
            if model.startswith(prefix):
                model = model[len(prefix) :]
                break

        provider = self.resolve_provider()
        if provider in ("ollama", "googleai", "vertexai"):
            return f"{provider}/{model}"

        raise ValueError(
            f"Unsupported or missing vector embedding provider '{self.settings.VECTOR_EMBED_PROVIDER}'. "
            "Expected 'ollama', 'googleai', or 'vertexai'."
        )

    @property
    def ai(self) -> Genkit:
        """Lazily initialize and return the Genkit instance configured for the active provider."""
        if self._ai is None:
            self._ai = self._create_genkit()
        return self._ai

    def _create_genkit(self) -> Genkit:
        """Instantiate Genkit with the Ollama, GoogleAI, or VertexAI plugin."""
        provider = self.resolve_provider()
        if provider == "ollama":
            base_url = (self.settings.VECTOR_EMBED_SERVER_URL or "").strip() or None
            logger.debug("initializing_genkit_ollama", base_url=base_url)
            return Genkit(plugins=[Ollama(base_url=base_url)])

        api_key = self._get_api_key()

        if provider == "googleai":
            logger.debug("initializing_genkit_googleai", has_api_key=bool(api_key))
            return Genkit(plugins=[GoogleAI(api_key=api_key)])

        if provider == "vertexai":
            project = self.settings.GOOGLE_CLOUD_PROJECT
            location = (self.settings.VERTEX_AI_LOCATION or "us-central1").strip()
            logger.debug(
                "initializing_genkit_vertexai",
                project=project,
                location=location,
                has_api_key=bool(api_key),
            )
            return Genkit(plugins=[VertexAI(project=project, location=location, api_key=api_key)])

        raise ValueError(
            f"Unsupported or missing vector embedding provider '{self.settings.VECTOR_EMBED_PROVIDER}'. "
            "Expected 'ollama', 'googleai', or 'vertexai'."
        )

    async def embed_text_async(
        self,
        text: str,
        *,
        config: Optional[dict[str, Any]] = None,
    ) -> list[float]:
        """Asynchronously compute and return an L2-normalized embedding vector for a string."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Input text for vector embedding must be a non-empty string.")

        embedder_name = self.resolve_embedder_name()
        provider = self.resolve_provider()
        dimensions = self.settings.VECTOR_EMBED_DIMENSIONS

        embed_config: dict[str, Any] = dict(config) if config else {}
        if provider in ("vertexai", "googleai") and dimensions and "output_dimensionality" not in embed_config:
            embed_config["output_dimensionality"] = dimensions

        response = await self.ai.embed(
            embedder=embedder_name,
            content=text,
            config=embed_config if embed_config else None,
        )
        if not response or not response[0].embedding:
            raise RuntimeError(f"Empty embedding response returned from embedder '{embedder_name}'.")

        raw_vector = response[0].embedding
        return normalize_vector(raw_vector, dimensions=dimensions)

    def embed_text(
        self,
        text: str,
        *,
        config: Optional[dict[str, Any]] = None,
    ) -> list[float]:
        """Compute and return an L2-normalized embedding vector for the given string.

        Safe to call from both synchronous scripts and environments with an active
        asyncio event loop (such as Jupyter notebooks or FastAPI endpoints).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    asyncio.run,
                    self.embed_text_async(text, config=config),
                )
                return future.result()

        return asyncio.run(self.embed_text_async(text, config=config))

    @staticmethod
    def cosine_scores(
        query_vector: Sequence[float],
        target_vectors: Sequence[Sequence[float]],
    ) -> list[float]:
        """Compute [0.0, 1.0] cosine similarity scores in index order."""
        return compute_cosine_scores(query_vector, target_vectors)

    @staticmethod
    def cosine_dot_product_scores(
        query_vector: Sequence[float],
        target_vectors: Sequence[Sequence[float]],
    ) -> list[float]:
        """Compute [0.0, 1.0] cosine dot-product similarity scores (for L2-normalized vectors) in index order."""
        return compute_dot_product_scores(query_vector, target_vectors)

    @staticmethod
    def euclidean_scores(
        query_vector: Sequence[float],
        target_vectors: Sequence[Sequence[float]],
    ) -> list[float]:
        """Compute [0.0, 1.0] Euclidean (L2) similarity scores in index order."""
        return compute_euclidean_scores(query_vector, target_vectors)

    @staticmethod
    def manhattan_scores(
        query_vector: Sequence[float],
        target_vectors: Sequence[Sequence[float]],
    ) -> list[float]:
        """Compute [0.0, 1.0] Manhattan (L1) similarity scores in index order."""
        return compute_manhattan_scores(query_vector, target_vectors)

    @staticmethod
    def compare_vectors(
        query_vector: Sequence[float],
        target_vectors: Sequence[Sequence[float]],
        metric: SimilarityMetricType = SimilarityMetric.COSINE,
    ) -> list[float]:
        """Compare query_vector against target_vectors using the specified metric and return [0.0, 1.0] scores."""
        return compute_similarity_scores(query_vector, target_vectors, metric=metric)


vector_service = VectorService()
