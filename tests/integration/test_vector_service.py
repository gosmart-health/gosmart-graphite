"""Integration test for embedding configuration resolved from Neo4j."""

import numpy as np
import pytest

from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG, EmbeddingService


def test_live_vector_embedding_from_neo4j() -> None:
    """Integration test against the active DEFAULT_EMBEDDING_TAG ('embedding-v1') LLM_Config stored in Neo4j.

    Automatically skips if Neo4j is unreachable or no active embedding LLM_Config exists.
    """
    svc = EmbeddingService()
    if not svc.is_configured():
        pytest.skip(f"No active '{DEFAULT_EMBEDDING_TAG}' LLM_Config configured in Neo4j; skipping live test.")

    clinical_note = "Patient presents with acute onset dyspnea and denies chest pain."
    vector = svc.embed_text(clinical_note)

    assert isinstance(vector, list)
    assert len(vector) > 0

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
