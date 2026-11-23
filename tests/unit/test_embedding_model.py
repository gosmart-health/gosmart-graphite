"""Modular unit tests for decoupled vector embedding models (Embedding & EmbedsRelation)."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from gosmart_graphite.model import (
    DEFAULT_EMBEDDING_VECTOR_DIMENSIONS,
    EMBEDDING_VECTOR_INDEX_NAME,
    AuditableNode,
    AuditableRelation,
    DiagnosticReportFieldTag,
    Embedding,
    EmbeddingNode,
    EmbedsRelation,
)


def test_embedding_constants_and_inheritance() -> None:
    assert DEFAULT_EMBEDDING_VECTOR_DIMENSIONS == 768
    assert EMBEDDING_VECTOR_INDEX_NAME == "embedding_vector_idx"
    assert EmbeddingNode is Embedding
    assert issubclass(Embedding, AuditableNode)
    assert issubclass(EmbedsRelation, AuditableRelation)
    assert Embedding.__label__ == "Embedding"
    assert EmbedsRelation.__type__ == "EMBEDS"
    assert DiagnosticReportFieldTag.FINDINGS == "FINDINGS"
    assert DiagnosticReportFieldTag.CONCLUSION == "CONCLUSION"


def test_embedding_auto_populates_size_and_normalizes_fields() -> None:
    emb = Embedding(
        target_node_id="  dr-node-1  ",
        modelTag="  embedding-v1  ",
        vector=[0.6, 0.8, 0.0],
        text="  Lungs are clear.  ",
        targetFieldTag="  FINDINGS  ",
        test_id="  t_emb_norm  ",
        ActedBy="pytest",
    )
    assert emb.modelTag == "embedding-v1"
    assert emb.targetFieldTag == "FINDINGS"
    assert emb.target_node_id == "dr-node-1"
    assert emb.test_id == "t_emb_norm"
    assert emb.size == 3
    assert emb.vector == [0.6, 0.8, 0.0]
    assert emb.text == "Lungs are clear."
    assert emb.Action == "CREATE"
    assert emb.id != ""

    # target_node_id is excluded from Neo4j properties serialization
    props = emb.to_neo4j_properties()
    assert "target_node_id" not in props
    assert props["id"] == emb.id
    assert props["modelTag"] == "embedding-v1"
    assert props["size"] == 3
    assert props["vector"] == [0.6, 0.8, 0.0]
    assert props["text"] == "Lungs are clear."
    assert props["targetFieldTag"] == "FINDINGS"
    assert props["test_id"] == "t_emb_norm"


def test_embedding_deterministic_id_idempotency_and_scoping() -> None:
    emb1 = Embedding(
        target_node_id="dr-1",
        modelTag="embedding-v1",
        size=3,
        vector=[0.6, 0.8, 0.0],
        text="Lungs are clear.",
        targetFieldTag=DiagnosticReportFieldTag.FINDINGS,
        test_id="t_scope",
    )
    emb2 = Embedding(
        target_node_id="dr-1",
        modelTag="embedding-v1",
        size=3,
        vector=[0.0, 0.6, 0.8],  # Updated vector payload -> same deterministic ID
        text="Updated findings narrative.",  # Updated source text -> same deterministic ID
        targetFieldTag=DiagnosticReportFieldTag.FINDINGS,
        test_id="t_scope",
    )
    assert emb1.id == emb2.id
    assert emb1.id == Embedding.compute_target_scoped_id(
        target_node_id="dr-1",
        model_tag="embedding-v1",
        target_field_tag=DiagnosticReportFieldTag.FINDINGS,
        test_id="t_scope",
    )

    # Different target node -> different ID
    emb_other_target = Embedding(
        target_node_id="dr-2",
        modelTag="embedding-v1",
        vector=[0.6, 0.8, 0.0],
        text="Lungs are clear.",
        targetFieldTag=DiagnosticReportFieldTag.FINDINGS,
        test_id="t_scope",
    )
    assert emb_other_target.id != emb1.id

    # Different targetFieldTag -> different ID
    emb_conclusion = Embedding(
        target_node_id="dr-1",
        modelTag="embedding-v1",
        vector=[0.6, 0.8, 0.0],
        text="Normal chest.",
        targetFieldTag=DiagnosticReportFieldTag.CONCLUSION,
        test_id="t_scope",
    )
    assert emb_conclusion.id != emb1.id

    # Different modelTag -> different ID (multi-model coexistence)
    emb_v2 = Embedding(
        target_node_id="dr-1",
        modelTag="embedding-v2",
        vector=[0.6, 0.8, 0.0],
        text="Lungs are clear.",
        targetFieldTag=DiagnosticReportFieldTag.FINDINGS,
        test_id="t_scope",
    )
    assert emb_v2.id != emb1.id

    # Production vs test_id isolation
    emb_prod = Embedding(
        target_node_id="dr-1",
        modelTag="embedding-v1",
        vector=[0.6, 0.8, 0.0],
        text="Lungs are clear.",
        targetFieldTag=DiagnosticReportFieldTag.FINDINGS,
    )
    assert emb_prod.id != emb1.id


@pytest.mark.parametrize(
    "kwargs",
    [
        {"modelTag": "", "vector": [1.0], "text": "Lungs are clear.", "targetFieldTag": "FINDINGS"},
        {"modelTag": "   ", "vector": [1.0], "text": "Lungs are clear.", "targetFieldTag": "FINDINGS"},
        {"modelTag": "embedding-v1", "vector": [1.0], "text": "Lungs are clear.", "targetFieldTag": ""},
        {"modelTag": "embedding-v1", "vector": [1.0], "targetFieldTag": "FINDINGS"},
        {"modelTag": "embedding-v1", "vector": [1.0], "text": "", "targetFieldTag": "FINDINGS"},
        {"modelTag": "embedding-v1", "vector": [1.0], "text": "   ", "targetFieldTag": "FINDINGS"},
        {"modelTag": "embedding-v1", "vector": [1.0], "text": None, "targetFieldTag": "FINDINGS"},
        {"modelTag": "embedding-v1", "vector": [1.0], "text": 123, "targetFieldTag": "FINDINGS"},
        {"modelTag": "embedding-v1", "vector": [], "text": "Lungs are clear.", "targetFieldTag": "FINDINGS"},
        {
            "modelTag": "embedding-v1",
            "vector": "not-a-vector",
            "text": "Lungs are clear.",
            "targetFieldTag": "FINDINGS",
        },
        {"modelTag": "embedding-v1", "vector": [True, 0.5], "text": "Lungs are clear.", "targetFieldTag": "FINDINGS"},
        {
            "modelTag": "embedding-v1",
            "vector": [math.nan, 0.5],
            "text": "Lungs are clear.",
            "targetFieldTag": "FINDINGS",
        },
        {
            "modelTag": "embedding-v1",
            "vector": [math.inf, 0.5],
            "text": "Lungs are clear.",
            "targetFieldTag": "FINDINGS",
        },
        {
            "modelTag": "embedding-v1",
            "size": 5,
            "vector": [0.6, 0.8],
            "text": "Lungs are clear.",
            "targetFieldTag": "FINDINGS",
        },
        {
            "modelTag": "embedding-v1",
            "size": -1,
            "vector": [0.6, 0.8],
            "text": "Lungs are clear.",
            "targetFieldTag": "FINDINGS",
        },
        {
            "modelTag": "embedding-v1",
            "size": True,
            "vector": [1.0],
            "text": "Lungs are clear.",
            "targetFieldTag": "FINDINGS",
        },
    ],
)
def test_embedding_validation_rejects_invalid_inputs(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Embedding(**kwargs)  # type: ignore[arg-type]


def test_compute_target_scoped_id_rejects_blank_arguments() -> None:
    with pytest.raises(ValueError, match="must all be non-empty"):
        Embedding.compute_target_scoped_id("", "embedding-v1", "FINDINGS")
    with pytest.raises(ValueError, match="must all be non-empty"):
        Embedding.compute_target_scoped_id("dr-1", "   ", "FINDINGS")
    with pytest.raises(ValueError, match="must all be non-empty"):
        Embedding.compute_target_scoped_id("dr-1", "embedding-v1", "")


def test_embeds_relation_deterministic_id_aspect_and_negation() -> None:
    rel1 = EmbedsRelation(
        start_node_id="emb-1",
        end_node_id="dr-1",
        aspect="  FINDINGS  ",
        test_id="t_rel",
        ActedBy="pytest",
    )
    rel2 = EmbedsRelation(
        start_node_id="emb-1",
        end_node_id="dr-1",
        aspect="FINDINGS",
        test_id="t_rel",
    )
    assert rel1.id == rel2.id
    assert rel1.aspect == "FINDINGS"
    assert rel1.to_negated_type() == "NOT_EMBEDS"
    assert rel1.to_positive_type() == "EMBEDS"
    assert rel1.is_negated() is False

    rel_blank_aspect = EmbedsRelation(
        start_node_id="emb-1",
        end_node_id="dr-1",
        aspect="   ",
    )
    assert rel_blank_aspect.aspect is None
