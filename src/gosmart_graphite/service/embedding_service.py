"""Vector Embedding Computation Service using Genkit (Ollama, Google AI, and Google Vertex AI)."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Optional, Sequence, Union

import numpy as np
from genkit import Genkit
from genkit_google_genai import GoogleAI, VertexAI
from genkit_ollama import Ollama

from gosmart_graphite.core.config import Settings, get_settings
from gosmart_graphite.core.logging import get_logger
from gosmart_graphite.model.llm import LLM_Config

if TYPE_CHECKING:
    from gosmart_graphite.service.llm_config_service import LLMConfigService

logger = get_logger("gosmart_gs.vector_service")

DEFAULT_EMBEDDING_TAG = "embedding-v1"

# These are just the basic prefixes. Refer to the provider's documents for
# asymmetric Q/A, and symmetric usages.

INSTRUCTION_PREFIXES: dict[str, dict[str, Any]] = {
    "nomic-embed-text": {
        "search": "search_query: ",
        "document": "search_document: ",
        "parameterStyle": "",
    },
    "qwen3-embedding": {
        "search": "Given the search query, retrieve relevant passages that answer the query\nQuery: ",
        "document": "",
        "is_parametric": False,
        "parameterStyle": "",
    },
    "quen3-embedding": {
        "search": "Given the search query, retrieve relevant passages that answer the query\nQuery: ",
        "document": "",
        "is_parametric": False,
        "parameterStyle": "",
    },
    "embeddinggemma-2": {
        "search": "task: search result | query: ",
        "document": "title: ${title} | text: ",  # If you have a title provide it instead of none
        "is_parametric": False,
        "parameterStyle": "",
    }
}


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
    denominator = q_norm * t_norms

    dots = targets @ q
    cos_sim = np.where(denominator > 0.0, dots / np.where(denominator > 0.0, denominator, 1.0), -1.0)
    cos_sim = np.clip(cos_sim, -1.0, 1.0)
    scores = np.clip((cos_sim + 1.0) / 2.0, 0.0, 1.0)
    # noinspection bad-return
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
    # noinspection bad-return
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
    # noinspection bad-return
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
    # noinspection bad-return
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


class EmbeddingService:
    """Computes L2-normalized text embeddings via dynamic, group-scoped LLM_Config + Genkit."""

    def __init__(
            self,
            config: Optional[LLM_Config] = None,
            llm_config_service: Optional[LLMConfigService] = None,
            ai: Optional[Genkit] = None,
            settings: Optional[Settings] = None,
    ) -> None:
        self._static_config: Optional[LLM_Config] = config
        self._llm_config_service: Optional[LLMConfigService] = llm_config_service
        self._ai: Optional[Genkit] = ai
        self.settings: Settings = settings or get_settings()
        self._client_cache: dict[tuple[Any, ...], Genkit] = {}
        self._cache_lock = threading.Lock()

    def _get_llm_config_service(self) -> LLMConfigService:
        if self._llm_config_service is not None:
            return self._llm_config_service
        from gosmart_graphite.service.llm_config_service import llm_config_service as default_llm_config_service

        return default_llm_config_service

    def _encryption_key(self) -> Optional[str]:
        if self.settings.LLM_CONFIG_ENCRYPTION_KEY is not None:
            val = self.settings.LLM_CONFIG_ENCRYPTION_KEY.get_secret_value().strip()
            return val if val else None
        return None

    def _resolve_api_key(self, cfg: LLM_Config) -> str:
        return cfg.resolve_api_key(encryption_key=self._encryption_key())

    def _is_config_usable(self, cfg: Optional[LLM_Config]) -> bool:
        if cfg is None:
            return False
        model = (cfg.model or "").strip()
        if not model:
            return False
        provider = self.resolve_provider(config=cfg)
        if provider == "ollama":
            return bool((cfg.url or "").strip())
        if provider in ("vertexai", "googleai"):
            return True
        return False

    def is_configured(
            self,
            *,
            group_id: Optional[str] = None,
            tag: str = DEFAULT_EMBEDDING_TAG,
            test_id: Optional[str] = None,
            llm_config: Optional[LLM_Config] = None,
    ) -> bool:
        """Return True if an embedding LLM_Config is configured and usable."""
        cfg = llm_config or self._static_config
        if cfg is None:
            try:
                cfg = self._get_llm_config_service().resolve_config_for_group_sync(
                    tag=tag,
                    group_id=group_id,
                    test_id=test_id,
                )
            except Exception as exc:
                logger.warning("llm_config_is_configured_lookup_failed", tag=tag, group_id=group_id, error=str(exc))
                return False
        return self._is_config_usable(cfg)

    async def is_configured_async(
            self,
            *,
            group_id: Optional[str] = None,
            tag: str = DEFAULT_EMBEDDING_TAG,
            test_id: Optional[str] = None,
            llm_config: Optional[LLM_Config] = None,
    ) -> bool:
        """Asynchronously return True if an embedding LLM_Config is configured and usable."""
        cfg = llm_config or self._static_config
        if cfg is None:
            try:
                cfg = await self._get_llm_config_service().resolve_config_for_group(
                    tag=tag,
                    group_id=group_id,
                    test_id=test_id,
                )
            except Exception as exc:
                logger.warning("llm_config_is_configured_lookup_failed", tag=tag, group_id=group_id, error=str(exc))
                return False
        return self._is_config_usable(cfg)

    def resolve_provider(
            self,
            config: Optional[LLM_Config] = None,
            model: Optional[str] = None,
            resolved_api_key: Optional[str] = None,
    ) -> str:
        """Determine the active provider ('ollama', 'googleai', or 'vertexai') from LLM_Config or model."""
        cfg = config or self._static_config
        target_model = (model or (cfg.model if cfg else "") or "").strip()
        provider = (cfg.provider if cfg else "").strip().lower()
        api_key = resolved_api_key if resolved_api_key is not None else (self._resolve_api_key(cfg) if cfg else "")
        project_id = (cfg.project_id if cfg else None) or self.settings.GOOGLE_CLOUD_PROJECT

        if target_model.startswith("ollama/"):
            return "ollama"
        if target_model.startswith("googleai/"):
            return "googleai"
        if target_model.startswith("vertexai/"):
            if api_key and not project_id:
                return "googleai"
            return "vertexai"

        if provider == "ollama":
            return "ollama"
        if provider in ("googleai", "google_ai", "gemini"):
            return "googleai"
        if provider in ("vertexai", "vertex", "vertex_ai", "google", "google_vertex"):
            if api_key and not project_id:
                return "googleai"
            return "vertexai"

        if cfg and (cfg.url or "").strip():
            return "ollama"

        return provider

    def resolve_embedder_name(
            self,
            config: Optional[LLM_Config] = None,
            model: Optional[str] = None,
            resolved_api_key: Optional[str] = None,
    ) -> str:
        """Return the fully-qualified Genkit embedder identifier (e.g. 'ollama/nomic-embed-text:latest')."""
        cfg = config or self._static_config
        raw_model = (model or (cfg.model if cfg else "") or "").strip()
        if not raw_model:
            raise ValueError("LLM_Config model is not configured.")

        provider = self.resolve_provider(config=cfg, model=raw_model, resolved_api_key=resolved_api_key)

        target_model = raw_model
        for prefix in ("ollama/", "googleai/", "vertexai/"):
            if target_model.startswith(prefix):
                target_model = target_model[len(prefix):]
                break

        if provider in ("ollama", "googleai", "vertexai"):
            return f"{provider}/{target_model}"

        raw_provider = cfg.provider if cfg else ""
        raise ValueError(
            f"Unsupported or missing vector embedding provider '{raw_provider}'. "
            "Expected 'ollama', 'googleai', or 'vertexai'."
        )

    def _get_or_create_genkit(
            self,
            cfg: LLM_Config,
            *,
            provider: str,
            api_key: str,
    ) -> Genkit:
        """Return an injected test client or a thread-safe cached Genkit client keyed by config fingerprint."""
        if self._ai is not None:
            return self._ai

        fingerprint = (
            provider,
            cfg.client_fingerprint(
                resolved_api_key=api_key,
                fallback_project_id=self.settings.GOOGLE_CLOUD_PROJECT,
            ),
        )
        with self._cache_lock:
            cached = self._client_cache.get(fingerprint)
            if cached is not None:
                return cached
            client = self._create_genkit(cfg, provider=provider, api_key=api_key)
            self._client_cache[fingerprint] = client
            return client

    def _create_genkit(
            self,
            cfg: LLM_Config,
            *,
            provider: str,
            api_key: str,
    ) -> Genkit:
        """Instantiate Genkit with the Ollama, GoogleAI, or VertexAI plugin from LLM_Config."""
        if provider == "ollama":
            base_url = (cfg.url or "").strip() or None
            logger.debug("initializing_genkit_ollama", base_url=base_url, tag=cfg.tag)
            return Genkit(plugins=[Ollama(base_url=base_url)])

        effective_key = api_key.strip() or None

        if provider == "googleai":
            logger.debug("initializing_genkit_googleai", has_api_key=bool(effective_key), tag=cfg.tag)
            return Genkit(plugins=[GoogleAI(api_key=effective_key)])

        if provider == "vertexai":
            project = cfg.project_id or self.settings.GOOGLE_CLOUD_PROJECT
            location = (cfg.location or "us-central1").strip()
            logger.debug(
                "initializing_genkit_vertexai",
                project=project,
                location=location,
                has_api_key=bool(effective_key),
                tag=cfg.tag,
            )
            return Genkit(plugins=[VertexAI(project=project, location=location, api_key=effective_key)])

        raise ValueError(
            f"Unsupported or missing vector embedding provider '{cfg.provider}'. "
            "Expected 'ollama', 'googleai', or 'vertexai'."
        )

    async def _resolve_runtime_config(
            self,
            *,
            tag: str,
            group_id: Optional[str],
            test_id: Optional[str],
            llm_config: Optional[LLM_Config],
    ) -> LLM_Config:
        cfg = llm_config or self._static_config
        if cfg is None:
            cfg = await self._get_llm_config_service().resolve_config_for_group(
                tag=tag,
                group_id=group_id,
                test_id=test_id,
            )
        if cfg is None:
            raise ValueError(f"LLM configuration for tag '{tag}' is not configured.")
        return cfg

    async def embed_text_async(
            self,
            text: str,
            model: Optional[str] = None,
            is_query: bool = False,
            *,
            group_id: Optional[str] = None,
            tag: str = DEFAULT_EMBEDDING_TAG,
            test_id: Optional[str] = None,
            title: str = "none",
            llm_config: Optional[LLM_Config] = None,
            config: Optional[dict[str, Any]] = None,
    ) -> list[float]:
        """Asynchronously compute and return an L2-normalized embedding vector for a string."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Input text for vector embedding must be a non-empty string.")

        cfg = await self._resolve_runtime_config(
            tag=tag,
            group_id=group_id,
            test_id=test_id,
            llm_config=llm_config,
        )
        resolved_key = self._resolve_api_key(cfg)
        target_model = (model or cfg.model or "").strip()
        provider = self.resolve_provider(config=cfg, model=target_model, resolved_api_key=resolved_key)
        embedder_name = self.resolve_embedder_name(config=cfg, model=target_model, resolved_api_key=resolved_key)

        mode: Literal["search", "document"] = "search" if is_query else "document"
        prefix = self.prefix_by_model(target_model, mode).replace("${title}", title)
        content = f"{prefix}{text}" if prefix else text

        logger.info(f"Embedding text: {content}")

        embed_config: dict[str, Any] = {}
        if cfg.extra_config:
            embed_config.update(cfg.extra_config)
        if config:
            embed_config.update(config)
        if provider in ("vertexai", "googleai") and cfg.dimensions and "output_dimensionality" not in embed_config:
            embed_config["output_dimensionality"] = cfg.dimensions

        ai_client = self._get_or_create_genkit(cfg, provider=provider, api_key=resolved_key)
        response = await ai_client.embed(
            embedder=embedder_name,
            content=content,
            config=embed_config if embed_config else None,
        )
        if not response or not response[0].embedding:
            raise RuntimeError(f"Empty embedding response returned from embedder '{embedder_name}'.")

        raw_vector = response[0].embedding
        if cfg.dimensions is not None and len(raw_vector) < cfg.dimensions:
            raise ValueError(
                f"Embedding vector length ({len(raw_vector)}) is smaller than configured dimensions ({cfg.dimensions})."
            )

        target_dim = cfg.dimensions_truncated or cfg.dimensions
        return normalize_vector(raw_vector, dimensions=target_dim)

    def embed_text(
        self,
        text: str,
        model: Optional[str] = None,
        is_query: bool = False,
        *,
        group_id: Optional[str] = None,
        tag: str = DEFAULT_EMBEDDING_TAG,
        test_id: Optional[str] = None,
        title: str = "none",
        llm_config: Optional[LLM_Config] = None,
        config: Optional[dict[str, Any]] = None,
    ) -> list[float]:
        """Compute and return an L2-normalized embedding vector for the given string.

        Safe to call from both synchronous scripts and environments with an active
        asyncio event loop (such as Jupyter notebooks or FastAPI endpoints).
        """
        coro = self.embed_text_async(
            text,
            model=model,
            is_query=is_query,
            group_id=group_id,
            tag=tag,
            test_id=test_id,
            title=title,
            llm_config=llm_config,
            config=config,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()

        return asyncio.run(coro)

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

    @staticmethod
    def prefix_by_model(model: Optional[str], mode: Literal["search", "document"]) -> str:
        """Return the instruction prefix for a given embedding model and mode ('search' or 'document')."""
        if not model or not model.strip():
            return ""

        model_lc = model.strip().lower()
        if "/" in model_lc:
            model_lc = model_lc.split("/")[-1]

        for key, instruction in INSTRUCTION_PREFIXES.items():
            if model_lc.startswith(key):
                prefix = instruction.get(mode)
                if prefix is not None:
                    return prefix
                break

        logger.warning(
            "unsupported_model_or_mode_prefix",
            model=model,
            mode=mode,
        )
        return ""


vector_service = EmbeddingService()
