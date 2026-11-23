"""Modular unit tests for SearchService, SearchResult, and SearchIncludeLabel."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gosmart_graphite.model import (
    EncounterNode,
    FHIRPatientNode,
    ImagingStudyNode,
    PatientNode,
    SearchIncludeLabel,
    SearchResult,
    StudyNode,
)
from gosmart_graphite.service.embedding_service import DEFAULT_EMBEDDING_TAG
from gosmart_graphite.service.search_service import (
    DEFAULT_SEARCH_LIMIT,
    SearchService,
)


class _FakeSyncResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __iter__(self):
        for row in self._rows:
            rec = MagicMock()
            rec.data.return_value = row
            yield rec


def _make_sync_client(responses: list[list[dict[str, Any]]]) -> tuple[MagicMock, list[tuple[str, dict[str, Any]]]]:
    """Build a mock Neo4jClient supporting get_sync_driver() and recording queries/params."""
    calls: list[tuple[str, dict[str, Any]]] = []
    idx = {"i": 0}

    def _run(query: str, parameters: dict[str, Any] | None = None, **kwargs: Any) -> _FakeSyncResult:
        merged = dict(parameters or {})
        merged.update(kwargs)
        calls.append((query, merged))
        rows = responses[idx["i"]] if idx["i"] < len(responses) else []
        idx["i"] += 1
        return _FakeSyncResult(rows)

    mock_session = MagicMock()
    mock_session.run.side_effect = _run
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = False

    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session

    mock_client = MagicMock()
    mock_client.settings = MagicMock(DB_DATABASE="neo4j")
    mock_client.get_sync_driver.return_value = mock_driver
    return mock_client, calls


def _make_async_client(responses: list[list[dict[str, Any]]]) -> tuple[MagicMock, list[tuple[str, dict[str, Any]]]]:
    """Build a mock Neo4jClient supporting async execute_query() and recording queries/params."""
    calls: list[tuple[str, dict[str, Any]]] = []
    idx = {"i": 0}

    async def _exec(query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        calls.append((query, dict(parameters or {})))
        rows = responses[idx["i"]] if idx["i"] < len(responses) else []
        idx["i"] += 1
        return rows

    mock_client = MagicMock()
    mock_client.execute_query = AsyncMock(side_effect=_exec)
    return mock_client, calls


def _sample_hit_row(
    *,
    node_id: str = "dr-node-1",
    node_label: str = "DiagnosticReport",
    score: float = 0.94,
    text: str = "Lungs are clear without consolidation.",
    target_field_tag: str = "FINDINGS",
    model_tag: str = DEFAULT_EMBEDDING_TAG,
    embedding_id: str = "emb-1",
    is_deleted: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "score": score,
        "node_label": node_label,
        "node_id": node_id,
        "text": text,
        "target_field_tag": target_field_tag,
        "model_tag": model_tag,
        "embedding_id": embedding_id,
        "is_deleted": is_deleted,
    }
    if extra:
        row.update(extra)
    return row


def test_search_models_and_defaults() -> None:
    assert DEFAULT_SEARCH_LIMIT == 20
    assert SearchIncludeLabel.ENCOUNTER == "Encounter"
    assert SearchIncludeLabel.FHIR_PATIENT == "FHIR_Patient"
    assert SearchIncludeLabel.IMAGING_STUDY == "ImagingStudy"
    assert SearchIncludeLabel.PATIENT == "Patient"
    assert SearchIncludeLabel.STUDY == "Study"

    res = SearchResult(
        score=0.91,
        node_label="DiagnosticReport",
        node_id="dr-1",
        text="No acute cardiopulmonary process.",
    )
    assert res.score == 0.91
    assert res.node_label == "DiagnosticReport"
    assert res.node_id == "dr-1"
    assert res.text == "No acute cardiopulmonary process."
    assert res.is_deleted is False
    assert res.encounter is None
    assert res.fhir_patient is None
    assert res.imaging_studies is None
    assert res.patient is None
    assert res.studies is None


@pytest.mark.anyio
async def test_blank_query_returns_empty_without_calling_embedder_or_db() -> None:
    mock_emb = MagicMock()
    mock_emb.embed_text = MagicMock()
    mock_emb.embed_text_async = AsyncMock()
    mock_client, async_calls = _make_async_client([])
    svc = SearchService(embedding_service=mock_emb, client=mock_client)

    assert svc.search("   ") == []
    assert await svc.search_async("") == []
    mock_emb.embed_text.assert_not_called()
    mock_emb.embed_text_async.assert_not_called()
    assert async_calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": -5},
        {"limit": True},
        {"min_score": -0.1},
        {"min_score": 1.5},
        {"min_score": True},
        {"max_group_depth": -1},
        {"max_group_depth": False},
        {"include": ["UnknownLabel"]},
    ],
)
def test_search_validates_parameters(kwargs: dict[str, Any]) -> None:
    svc = SearchService(embedding_service=MagicMock(), client=MagicMock())
    with pytest.raises(ValueError):
        svc.search("pneumonia", **kwargs)


@pytest.mark.anyio
async def test_search_async_unscoped_embeds_query_and_hydrates_results() -> None:
    mock_emb = MagicMock()
    mock_emb.embed_text_async = AsyncMock(return_value=[0.6, 0.8, 0.0])
    mock_client, calls = _make_async_client(
        [
            [
                {
                    "raw_hit_count": 2,
                    "results": [
                        _sample_hit_row(node_id="dr-1", score=0.95, text="Lungs clear.", target_field_tag="FINDINGS"),
                        _sample_hit_row(
                            node_id="dr-2", score=0.82, text="Normal chest.", target_field_tag="CONCLUSION"
                        ),
                    ],
                }
            ]
        ]
    )
    svc = SearchService(embedding_service=mock_emb, client=mock_client)

    results = await svc.search_async("clear lungs")

    mock_emb.embed_text_async.assert_awaited_once_with(
        "clear lungs",
        is_query=True,
        group_id=None,
        tag=DEFAULT_EMBEDDING_TAG,
        test_id=None,
    )
    assert len(calls) == 1
    cypher, params = calls[0]
    assert "db.index.vector.queryNodes" in cypher
    assert "e.test_id IS NULL" in cypher
    assert "allowed_group_ids" not in cypher
    assert "OPTIONAL MATCH" not in cypher
    assert params["limit"] == 20
    assert params["tag"] == DEFAULT_EMBEDDING_TAG
    assert params["include_deleted"] is False

    assert len(results) == 2
    assert results[0].node_id == "dr-1"
    assert results[0].node_label == "DiagnosticReport"
    assert results[0].score == 0.95
    assert results[0].text == "Lungs clear."
    assert results[0].target_field_tag == "FINDINGS"
    assert results[0].is_deleted is False
    assert results[0].encounter is None
    assert results[0].imaging_studies is None


def test_search_sync_with_group_test_id_and_include_hydrates_all_context_nodes() -> None:
    enc = EncounterNode(resource_id="enc-1", resource='{"resourceType":"Encounter","id":"enc-1"}', test_id="t_s1")
    fp = FHIRPatientNode(
        resource_id="pat-1",
        resource='{"resourceType":"Patient","id":"pat-1"}',
        name=["Jane Doe"],
        test_id="t_s1",
    )
    img = ImagingStudyNode(
        resource_id="img-1",
        resource='{"resourceType":"ImagingStudy","id":"img-1"}',
        studyUid="1.2.3.4",
        test_id="t_s1",
    )
    pat = PatientNode(patient_id="MRN-100", patient_name="DOE^JANE", test_id="t_s1")
    study = StudyNode(study_instance_uid="1.2.3.4", study_description="CT CHEST", test_id="t_s1")

    mock_emb = MagicMock()
    mock_emb.embed_text = MagicMock(return_value=[1.0, 0.0, 0.0])
    mock_client, calls = _make_sync_client(
        [
            [
                {
                    "raw_hit_count": 1,
                    "results": [
                        _sample_hit_row(
                            node_id="dr-1",
                            score=0.98,
                            text="Mild cardiomegaly.",
                            is_deleted=True,
                            extra={
                                "encounter_props": enc.model_dump(by_alias=True),
                                "fhir_patient_props": fp.model_dump(by_alias=True),
                                "imaging_studies_props": [img.model_dump(by_alias=True)],
                                "patient_props": pat.model_dump(),
                                "studies_props": [study.model_dump()],
                            },
                        )
                    ],
                }
            ]
        ]
    )
    svc = SearchService(embedding_service=mock_emb, client=mock_client)

    results = svc.search(
        "cardiomegaly",
        group_id="grp-hospital-1",
        limit=5,
        min_score=0.75,
        include=[
            SearchIncludeLabel.ENCOUNTER,
            SearchIncludeLabel.FHIR_PATIENT,
            SearchIncludeLabel.IMAGING_STUDY,
            SearchIncludeLabel.PATIENT,
            SearchIncludeLabel.STUDY,
        ],
        target_labels=["DiagnosticReport"],
        target_field_tags=["CONCLUSION"],
        include_deleted=True,
        test_id="t_s1",
    )

    mock_emb.embed_text.assert_called_once_with(
        "cardiomegaly",
        is_query=True,
        group_id="grp-hospital-1",
        tag=DEFAULT_EMBEDDING_TAG,
        test_id="t_s1",
    )
    assert len(calls) == 1
    cypher, params = calls[0]
    assert "BELONGS_TO*1.." in cypher
    assert "owning_group_id IN allowed_group_ids" in cypher
    assert "type(rel) IN $upward_rel_types" in cypher
    assert "NOT 'Group' IN labels(b)" in cypher
    assert "e.test_id = $test_id" in cypher
    assert "encounter_props" in cypher
    assert "fhir_patient_props" in cypher
    assert "imaging_studies_props" in cypher
    assert "patient_props" in cypher
    assert "studies_props" in cypher
    assert params["group_id"] == "grp-hospital-1"
    assert "BELONGS_TO" in params["upward_rel_types"]
    assert params["limit"] == 5
    assert params["min_score"] == 0.75
    assert params["include_deleted"] is True
    assert params["test_id"] == "t_s1"
    assert params["target_labels"] == ["DiagnosticReport"]
    assert params["target_field_tags"] == ["CONCLUSION"]

    assert len(results) == 1
    hit = results[0]
    assert hit.is_deleted is True
    assert hit.encounter is not None and hit.encounter.resource_id == "enc-1"
    assert hit.fhir_patient is not None and hit.fhir_patient.resource_id == "pat-1"
    assert hit.imaging_studies is not None and len(hit.imaging_studies) == 1
    assert hit.imaging_studies[0].studyUid == "1.2.3.4"
    assert hit.patient is not None and hit.patient.patient_id == "MRN-100"
    assert hit.studies is not None and len(hit.studies) == 1
    assert hit.studies[0].study_instance_uid == "1.2.3.4"


@pytest.mark.anyio
async def test_search_async_triggers_exact_cosine_fallback_when_hnsw_saturated() -> None:
    mock_emb = MagicMock()
    mock_emb.embed_text_async = AsyncMock(return_value=[1.0, 0.0, 0.0])
    # First call (HNSW) returns candidate_k=100 raw hits but 0 survive group filter -> triggers fallback
    mock_client, calls = _make_async_client(
        [
            [{"raw_hit_count": 100, "results": []}],
            [
                {
                    "results": [
                        _sample_hit_row(node_id="dr-small-tenant", score=0.88, text="Small tenant hit."),
                    ]
                }
            ],
        ]
    )
    svc = SearchService(embedding_service=mock_emb, client=mock_client)

    results = await svc.search_async("nodule", group_id="grp-small", limit=5)

    assert len(calls) == 2
    assert "db.index.vector.queryNodes" in calls[0][0]
    assert "vector.similarity.cosine" in calls[1][0]
    assert len(results) == 1
    assert results[0].node_id == "dr-small-tenant"
    assert results[0].score == 0.88


@pytest.mark.anyio
async def test_search_async_returns_empty_when_group_not_found() -> None:
    mock_emb = MagicMock()
    mock_emb.embed_text_async = AsyncMock(return_value=[1.0, 0.0, 0.0])
    # Group preamble filters out all rows -> 0 records returned -> no fallback triggered
    mock_client, calls = _make_async_client([[]])
    svc = SearchService(embedding_service=mock_emb, client=mock_client)

    results = await svc.search_async("pneumonia", group_id="grp-nonexistent", limit=5)

    assert results == []
    assert len(calls) == 1
