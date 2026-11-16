"""Live Gemini embedding tests requiring an API key."""

import os
from pathlib import Path

import numpy as np
import pytest

from gosmart_graphite.model.llm import LLM_Config
from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG, EmbeddingService


def test_live_gemini_embedding_from_env() -> None:
    """Integration test against Google Gemini embedding ('gemini-embedding-001') via LLM_Config.

    Automatically skips if no Gemini/Vertex API key environment variable is set.
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, v = stripped.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    api_key = (
        os.environ.get("GOSMART_GS_VERTEX_AI_API_KEY")
        or os.environ.get("VERTEX_AI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        pytest.skip("No Vertex/Gemini API key set in environment; skipping live Gemini embedding test.")

    gemini_cfg = LLM_Config(
        tag=DEFAULT_EMBEDDING_TAG,
        provider="vertexai",
        model="gemini-embedding-001",
        dimensions=768,
        api_key=api_key,
    )
    svc = EmbeddingService(config=gemini_cfg)

    clinical_note = "Patient presents with acute onset dyspnea and denies chest pain."
    vector = svc.embed_text(clinical_note)

    assert isinstance(vector, list)
    assert len(vector) == 768

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
