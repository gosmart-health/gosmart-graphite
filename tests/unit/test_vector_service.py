"""Unit and integration tests for EmbeddingService (dynamic LLM_Config + Genkit)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from gosmart_graphite.core.config import Settings
from gosmart_graphite.model.llm import LLM_Config
from gosmart_graphite.service import VectorService
from gosmart_graphite.service.embedding_service import (
    DEFAULT_EMBEDDING_TAG,
    EmbeddingService,
    SimilarityMetric,
    compute_cosine_scores,
    compute_similarity_scores,
    normalize_vector,
)


def test_normalize_vector_unit_length() -> None:
    raw = [3.0, 4.0, 0.0]
    normed = normalize_vector(raw)
    assert len(normed) == 3
    assert np.isclose(normed[0], 0.6)
    assert np.isclose(normed[1], 0.8)
    assert np.isclose(normed[2], 0.0)
    assert np.isclose(np.linalg.norm(normed), 1.0)


def test_normalize_vector_zero_norm() -> None:
    raw = [0.0, 0.0, 0.0]
    normed = normalize_vector(raw)
    assert normed == [0.0, 0.0, 0.0]


def test_normalize_vector_truncation_and_validation() -> None:
    raw = [1.0, 2.0, 2.0, 10.0, 20.0]
    normed = normalize_vector(raw, dimensions=3)
    assert len(normed) == 3
    assert np.isclose(np.linalg.norm(normed), 1.0)

    with pytest.raises(ValueError, match="smaller than configured dimensions"):
        normalize_vector([1.0, 2.0], dimensions=4)

    with pytest.raises(ValueError, match="Cannot normalize an empty vector"):
        normalize_vector([])


def test_similarity_metrics_0_to_1_scores() -> None:
    assert VectorService is EmbeddingService

    query = normalize_vector([1.0, 0.0, 0.0])
    targets = [
        normalize_vector([1.0, 0.0, 0.0]),  # identical -> score 1.0
        normalize_vector([0.0, 1.0, 0.0]),  # orthogonal -> cosine/dot 0.5
        normalize_vector([-1.0, 0.0, 0.0]),  # opposite -> cosine/dot 0.0
    ]

    cos_scores = EmbeddingService.cosine_scores(query, targets)
    dot_scores = EmbeddingService.cosine_dot_product_scores(query, targets)
    euc_scores = EmbeddingService.euclidean_scores(query, targets)
    man_scores = EmbeddingService.manhattan_scores(query, targets)

    assert len(cos_scores) == 3
    assert np.allclose(cos_scores, [1.0, 0.5, 0.0], atol=1e-6)
    assert np.allclose(dot_scores, [1.0, 0.5, 0.0], atol=1e-6)

    # Euclidean: dists are 0.0, sqrt(2), 2.0 -> 1 / (1 + d)
    assert np.isclose(euc_scores[0], 1.0)
    assert np.isclose(euc_scores[1], 1.0 / (1.0 + np.sqrt(2.0)), atol=1e-5)
    assert np.isclose(euc_scores[2], 1.0 / 3.0, atol=1e-5)
    assert euc_scores[0] > euc_scores[1] > euc_scores[2] >= 0.0

    # Manhattan: dists are 0.0, 2.0, 2.0 -> 1 / (1 + d)
    assert np.isclose(man_scores[0], 1.0)
    assert np.isclose(man_scores[1], 1.0 / 3.0, atol=1e-5)
    assert np.isclose(man_scores[2], 1.0 / 3.0, atol=1e-5)
    assert all(0.0 <= s <= 1.0 for s in man_scores)

    # Dispatch via compare_vectors / compute_similarity_scores
    assert EmbeddingService.compare_vectors(query, targets, SimilarityMetric.COSINE) == cos_scores
    assert EmbeddingService.compare_vectors(query, targets, "cosine_dot_product") == dot_scores
    assert EmbeddingService.compare_vectors(query, targets, "euclidean") == euc_scores
    assert EmbeddingService.compare_vectors(query, targets, "manhattan") == man_scores
    assert compute_similarity_scores(query, []) == []

    with pytest.raises(ValueError, match="Vector dimension mismatch"):
        compute_cosine_scores([1.0, 0.0], [[1.0, 0.0, 0.0]])

    with pytest.raises(ValueError, match="Unsupported similarity metric"):
        compute_similarity_scores(query, targets, "invalid_metric")  # type: ignore[arg-type]


def test_prefix_by_model() -> None:
    # nomic-embed-text
    assert EmbeddingService.prefix_by_model("nomic-embed-text", "search") == "search_query: "
    assert EmbeddingService.prefix_by_model("nomic-embed-text", "document") == "search_document: "
    assert EmbeddingService.prefix_by_model("ollama/nomic-embed-text:latest", "search") == "search_query: "
    assert EmbeddingService.prefix_by_model("ollama/nomic-embed-text:latest", "document") == "search_document: "

    # qwen3-embedding
    assert "Query:" in EmbeddingService.prefix_by_model("qwen3-embedding", "search")
    assert EmbeddingService.prefix_by_model("qwen3-embedding", "document") == ""

    # Empty / None / Unsupported models
    assert EmbeddingService.prefix_by_model(None, "search") == ""
    assert EmbeddingService.prefix_by_model("", "search") == ""
    assert EmbeddingService.prefix_by_model("text-embedding-004", "search") == ""


def test_embedding_service_ollama_mock() -> None:
    cfg = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="ollama",
        url="http://10.200.0.103:11434",
        model="nomic-embed-text:latest",
        dimensions=4,
    )

    mock_ai = MagicMock()
    mock_ai.embed = AsyncMock(return_value=[SimpleNamespace(embedding=[1.0, 2.0, 2.0, 0.0])])

    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)

    svc = EmbeddingService(config=cfg, ai=mock_ai, settings=settings)
    assert svc.is_configured() is True
    assert svc.resolve_provider() == "ollama"
    assert svc.resolve_embedder_name() == "ollama/nomic-embed-text:latest"

    # Document embedding (default is_query=False)
    vec = svc.embed_text("Patient presents with acute onset dyspnea and denies chest pain.")
    assert len(vec) == 4
    assert np.isclose(np.linalg.norm(vec), 1.0)
    mock_ai.embed.assert_awaited_once_with(
        embedder="ollama/nomic-embed-text:latest",
        content="search_document: Patient presents with acute onset dyspnea and denies chest pain.",
        config=None,
    )

    # Query embedding (is_query=True)
    mock_ai.embed.reset_mock()
    vec_q = svc.embed_text("acute onset dyspnea", is_query=True)
    assert len(vec_q) == 4
    mock_ai.embed.assert_awaited_once_with(
        embedder="ollama/nomic-embed-text:latest",
        content="search_query: acute onset dyspnea",
        config=None,
    )


def test_embedding_service_vertexai_mock() -> None:
    cfg = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="vertexai",
        model="gemini-embedding-001",
        dimensions=3,
        location="us-central1",
        project_id="test-gcp-project",
    )

    mock_ai = MagicMock()
    mock_ai.embed = AsyncMock(return_value=[SimpleNamespace(embedding=[0.0, 3.0, 4.0])])

    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)

    svc = EmbeddingService(config=cfg, ai=mock_ai, settings=settings)
    assert svc.is_configured() is True
    assert svc.resolve_provider() == "vertexai"
    assert svc.resolve_embedder_name() == "vertexai/gemini-embedding-001"

    vec = svc.embed_text("Normal chest radiograph.")
    assert len(vec) == 3
    assert np.isclose(np.linalg.norm(vec), 1.0)
    mock_ai.embed.assert_awaited_once_with(
        embedder="vertexai/gemini-embedding-001",
        content="Normal chest radiograph.",
        config={"output_dimensionality": 3},
    )


def test_embedding_service_dimensions_truncated_matryoshka() -> None:
    cfg = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="vertexai",
        model="gemini-embedding-001",
        dimensions=6,
        dimensions_truncated=3,
        project_id="test-gcp-project",
    )

    mock_ai = MagicMock()
    mock_ai.embed = AsyncMock(return_value=[SimpleNamespace(embedding=[3.0, 4.0, 0.0, 10.0, 20.0, 30.0])])

    svc = EmbeddingService(config=cfg, ai=mock_ai, settings=Settings(_env_file=None))
    vec = svc.embed_text("Normal chest radiograph.")
    assert len(vec) == 3
    assert np.isclose(vec[0], 0.6)
    assert np.isclose(vec[1], 0.8)
    assert np.isclose(np.linalg.norm(vec), 1.0)
    mock_ai.embed.assert_awaited_once_with(
        embedder="vertexai/gemini-embedding-001",
        content="Normal chest radiograph.",
        config={"output_dimensionality": 6},
    )


def test_embedding_service_client_cache_invalidation_on_config_change() -> None:
    cfg_v1 = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="ollama",
        url="http://localhost:11434",
        model="nomic-embed-text:latest",
    )
    cfg_v2 = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="ollama",
        url="http://10.200.0.103:11434",
        model="nomic-embed-text:latest",
    )

    mock_llm_svc = MagicMock()
    mock_llm_svc.resolve_config_for_group = AsyncMock(side_effect=[cfg_v1, cfg_v1, cfg_v2])

    created_clients: list[MagicMock] = []

    def _fake_genkit(plugins=None):
        client = MagicMock()
        client.embed = AsyncMock(return_value=[SimpleNamespace(embedding=[1.0, 0.0])])
        created_clients.append(client)
        return client

    with patch("gosmart_graphite.service.embedding_service.Genkit", side_effect=_fake_genkit) as genkit_cls:
        svc = EmbeddingService(llm_config_service=mock_llm_svc, settings=Settings(_env_file=None))
        svc.embed_text("First text")
        svc.embed_text("Second text same config")
        svc.embed_text("Third text updated url")

    assert genkit_cls.call_count == 2
    assert len(created_clients) == 2


def test_vector_service_unconfigured() -> None:
    mock_llm_svc = MagicMock()
    mock_llm_svc.resolve_config_for_group_sync.return_value = None
    mock_llm_svc.resolve_config_for_group = AsyncMock(return_value=None)

    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
    svc = EmbeddingService(llm_config_service=mock_llm_svc, settings=settings)
    assert svc.is_configured() is False
    with pytest.raises(ValueError, match="not configured"):
        svc.embed_text("Sample text")
