"""Unit and integration tests for VectorService (Genkit Ollama, Google AI, & Vertex AI)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from core.config import Settings
from service.vector_service import (
    SimilarityMetric,
    VectorService,
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
    query = normalize_vector([1.0, 0.0, 0.0])
    targets = [
        normalize_vector([1.0, 0.0, 0.0]),  # identical -> score 1.0
        normalize_vector([0.0, 1.0, 0.0]),  # orthogonal -> cosine/dot 0.5
        normalize_vector([-1.0, 0.0, 0.0]),  # opposite -> cosine/dot 0.0
    ]

    cos_scores = VectorService.cosine_scores(query, targets)
    dot_scores = VectorService.cosine_dot_product_scores(query, targets)
    euc_scores = VectorService.euclidean_scores(query, targets)
    man_scores = VectorService.manhattan_scores(query, targets)

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
    assert VectorService.compare_vectors(query, targets, SimilarityMetric.COSINE) == cos_scores
    assert VectorService.compare_vectors(query, targets, "cosine_dot_product") == dot_scores
    assert VectorService.compare_vectors(query, targets, "euclidean") == euc_scores
    assert VectorService.compare_vectors(query, targets, "manhattan") == man_scores
    assert compute_similarity_scores(query, []) == []

    with pytest.raises(ValueError, match="Vector dimension mismatch"):
        compute_cosine_scores([1.0, 0.0], [[1.0, 0.0, 0.0]])

    with pytest.raises(ValueError, match="Unsupported similarity metric"):
        compute_similarity_scores(query, targets, "invalid_metric")  # type: ignore[arg-type]


def test_vector_service_ollama_mock() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(
            _env_file=None,
            VECTOR_EMBED_PROVIDER="ollama",
            VECTOR_EMBED_SERVER_URL="http://10.200.0.103:11434",
            VECTOR_EMBED_MODEL="nomic-embed-text:latest",
            VECTOR_EMBED_DIMENSIONS=4,
        )

    mock_ai = MagicMock()
    mock_ai.embed = AsyncMock(return_value=[SimpleNamespace(embedding=[1.0, 2.0, 2.0, 0.0])])

    svc = VectorService(settings=settings, ai=mock_ai)
    assert svc.is_configured() is True
    assert svc.resolve_provider() == "ollama"
    assert svc.resolve_embedder_name() == "ollama/nomic-embed-text:latest"

    vec = svc.embed_text("Patient presents with acute onset dyspnea and denies chest pain.")
    assert len(vec) == 4
    assert np.isclose(np.linalg.norm(vec), 1.0)
    mock_ai.embed.assert_awaited_once_with(
        embedder="ollama/nomic-embed-text:latest",
        content="Patient presents with acute onset dyspnea and denies chest pain.",
        config=None,
    )


def test_vector_service_vertexai_mock() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(
            _env_file=None,
            VECTOR_EMBED_PROVIDER="vertexai",
            VECTOR_EMBED_MODEL="gemini-embedding-001",
            VECTOR_EMBED_DIMENSIONS=3,
            GOOGLE_CLOUD_PROJECT="test-gcp-project",
            VERTEX_AI_LOCATION="us-central1",
        )

    mock_ai = MagicMock()
    mock_ai.embed = AsyncMock(return_value=[SimpleNamespace(embedding=[0.0, 3.0, 4.0])])

    svc = VectorService(settings=settings, ai=mock_ai)
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


def test_vector_service_unconfigured() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
    svc = VectorService(settings=settings)
    assert svc.is_configured() is False
    with pytest.raises(ValueError, match="not configured"):
        svc.embed_text("Sample text")


def test_live_vector_embedding_from_env() -> None:
    """Integration test against the configured vector embedding endpoint in .env.

    Automatically skips if .env does not contain the vector embedding configuration.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    settings = Settings(_env_file=str(env_path) if env_path.exists() else None)
    svc = VectorService(settings=settings)

    if not svc.is_configured():
        pytest.skip("Vector embedding environment variables are not configured in .env; skipping live test.")

    clinical_note = "Patient presents with acute onset dyspnea and denies chest pain."
    vector = svc.embed_text(clinical_note)

    assert isinstance(vector, list)
    assert len(vector) > 0
    if settings.VECTOR_EMBED_DIMENSIONS:
        assert len(vector) == settings.VECTOR_EMBED_DIMENSIONS

    norm = float(np.linalg.norm(np.asarray(vector, dtype=np.float32)))
    assert np.isclose(norm, 1.0, atol=1e-5)

    # Compare query against candidate clinical sentences
    candidates = [
        svc.embed_text("Patient reports shortness of breath without chest discomfort."),
        svc.embed_text("Routine annual dental cleaning and fluoride treatment."),
    ]
    scores = svc.cosine_dot_product_scores(vector, candidates)
    assert len(scores) == 2
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores[0] > scores[1]


def test_live_gemini_embedding_from_env() -> None:
    """Integration test against Google Gemini embedding ('gemini-embedding-001').

    Automatically skips if GOSMART_GS_VERTEX_AI_API_KEY is not configured in .env.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    base_settings = Settings(_env_file=str(env_path) if env_path.exists() else None)

    api_key = (
        base_settings.VERTEX_AI_API_KEY.get_secret_value().strip()
        if base_settings.VERTEX_AI_API_KEY is not None
        else ""
    )
    if not api_key:
        pytest.skip("GOSMART_GS_VERTEX_AI_API_KEY is not set in .env; skipping live Gemini embedding test.")

    gemini_settings = base_settings.model_copy(
        update={
            "VECTOR_EMBED_PROVIDER": "vertexai",
            "VECTOR_EMBED_MODEL": "gemini-embedding-001",
            "VECTOR_EMBED_DIMENSIONS": base_settings.VECTOR_EMBED_DIMENSIONS or 768,
        }
    )
    svc = VectorService(settings=gemini_settings)

    clinical_note = "Patient presents with acute onset dyspnea and denies chest pain."
    vector = svc.embed_text(clinical_note)

    assert isinstance(vector, list)
    assert len(vector) == (base_settings.VECTOR_EMBED_DIMENSIONS or 768)

    norm = float(np.linalg.norm(np.asarray(vector, dtype=np.float32)))
    assert np.isclose(norm, 1.0, atol=1e-5)

    candidates = [
        svc.embed_text("Patient reports shortness of breath without chest discomfort."),
        svc.embed_text("Routine annual dental cleaning and fluoride treatment."),
    ]
    scores = svc.cosine_dot_product_scores(vector, candidates)
    assert len(scores) == 2
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores[0] > scores[1]
