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
    assert cypher.startswith("CYPHER 25")
    assert "SEARCH e IN (" in cypher
    assert "VECTOR INDEX `embedding_vector_idx`" in cypher
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
    assert "SEARCH e IN (" in calls[0][0]
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


@pytest.mark.anyio
async def test_search_async_falls_back_to_query_nodes_when_cypher25_unsupported() -> None:
    from neo4j.exceptions import CypherSyntaxError

    mock_emb = MagicMock()
    mock_emb.embed_text_async = AsyncMock(return_value=[1.0, 0.0, 0.0])

    calls: list[tuple[str, dict[str, Any]]] = []

    async def _exec(query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        calls.append((query, dict(parameters or {})))
        if "SEARCH e IN (" in query:
            raise CypherSyntaxError("Invalid input '25': expected Cypher version")
        return [{"raw_hit_count": 1, "results": [_sample_hit_row(node_id="dr-legacy-1", score=0.91)]}]

    mock_client = MagicMock()
    mock_client.execute_query = AsyncMock(side_effect=_exec)
    svc = SearchService(embedding_service=mock_emb, client=mock_client)

    first = await svc.search_async("pneumonia")
    second = await svc.search_async("pneumonia")

    assert len(first) == 1 and first[0].node_id == "dr-legacy-1"
    assert len(second) == 1 and second[0].node_id == "dr-legacy-1"
    # First call tried Cypher 25 SEARCH then fell back; second call went straight to queryNodes
    assert len(calls) == 3
    assert "SEARCH e IN (" in calls[0][0]
    assert "db.index.vector.queryNodes" in calls[1][0]
    assert "db.index.vector.queryNodes" in calls[2][0]


def test_search_sync_falls_back_to_query_nodes_and_caches() -> None:
    from neo4j.exceptions import CypherSyntaxError

    mock_emb = MagicMock()
    mock_emb.embed_text = MagicMock(return_value=[1.0, 0.0, 0.0])

    calls: list[tuple[str, dict[str, Any]]] = []

    def _run(query: str, parameters: dict[str, Any] | None = None, **kwargs: Any) -> _FakeSyncResult:
        calls.append((query, dict(parameters or {})))
        if "SEARCH e IN (" in query:
            raise CypherSyntaxError("Invalid input 'SEARCH'")
        return _FakeSyncResult([{"raw_hit_count": 1, "results": [_sample_hit_row(node_id="dr-legacy-sync")]}])

    mock_session = MagicMock()
    mock_session.run.side_effect = _run
    mock_session.__enter__.return_value = mock_session
    mock_session.__exit__.return_value = False
    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session
    mock_client = MagicMock()
    mock_client.settings = MagicMock(DB_DATABASE="neo4j")
    mock_client.get_sync_driver.return_value = mock_driver

    svc = SearchService(embedding_service=mock_emb, client=mock_client)
    res1 = svc.search("dyspnea")
    res2 = svc.search("dyspnea")

    assert [r.node_id for r in res1] == ["dr-legacy-sync"]
    assert [r.node_id for r in res2] == ["dr-legacy-sync"]
    assert len(calls) == 3
    assert "SEARCH e IN (" in calls[0][0]
    assert "db.index.vector.queryNodes" in calls[1][0]
    assert "db.index.vector.queryNodes" in calls[2][0]


@pytest.mark.anyio
async def test_search_async_propagates_non_syntax_errors_without_fallback() -> None:
    mock_emb = MagicMock()
    mock_emb.embed_text_async = AsyncMock(return_value=[1.0, 0.0, 0.0])
    mock_client = MagicMock()
    mock_client.execute_query = AsyncMock(side_effect=RuntimeError("connection reset"))

    svc = SearchService(embedding_service=mock_emb, client=mock_client)
    with pytest.raises(RuntimeError, match="connection reset"):
        await svc.search_async("pneumonia")

    assert mock_client.execute_query.await_count == 1
    assert svc._use_cypher25_search is True


def test_search_service_validates_and_escapes_index_name() -> None:
    with pytest.raises(ValueError, match="index_name"):
        SearchService(embedding_service=MagicMock(), client=MagicMock(), index_name="   ")

    with pytest.raises(ValueError, match="control characters"):
        SearchService(embedding_service=MagicMock(), client=MagicMock(), index_name="bad\nindex")

    mock_emb = MagicMock()
    mock_emb.embed_text = MagicMock(return_value=[1.0, 0.0, 0.0])
    mock_client, calls = _make_sync_client([[{"raw_hit_count": 0, "results": []}]])
    svc = SearchService(embedding_service=mock_emb, client=mock_client, index_name="my`idx")

    assert svc.search("test") == []
    assert "VECTOR INDEX `my``idx`" in calls[0][0]


def _ensure_test_nlu_bundle(tmp_path: Any) -> Any:
    """Return the repo's trained NLU bundle if present, else train a temporary one from training/."""
    from pathlib import Path

    from gosmart_clinical_nlu import load_training_dir, save_bundle, train_intent_models

    repo_root = Path(__file__).resolve().parents[2]
    bundled = repo_root / "nlu_models" / "intent_models.zip"
    if bundled.is_file():
        return bundled
    corpus = load_training_dir(repo_root / "training")
    models = train_intent_models(corpus)
    out = tmp_path / "intent_models.zip"
    return save_bundle(models, out)


def test_parse_utterance_proxies_to_loaded_models_and_forwards_ref_date() -> None:
    from datetime import datetime
    from unittest.mock import patch

    from gosmart_clinical_nlu import ParsedUtterance

    stub_models = MagicMock(name="stub_intent_models")
    expected = ParsedUtterance(
        status="matched",
        intent="find_study",
        confidence=0.99,
        threshold=0.6,
        candidates={"find_study": 0.99, "get_report": 0.01},
    )
    svc = SearchService(
        embedding_service=MagicMock(),
        client=MagicMock(),
        intent_models=stub_models,
    )
    assert svc.nlu_ready is True

    ref = datetime(2026, 11, 23, 10, 0, 0)
    with patch("gosmart_graphite.service.search_service._nlu_parse_utterance", return_value=expected) as mock_parse:
        result = svc.parse_utterance("  Find a CT for patient 12345678  ", ref_date=ref)

    assert result == expected
    mock_parse.assert_called_once_with("Find a CT for patient 12345678", stub_models, ref_date=ref)


def test_parse_utterance_validates_non_string() -> None:
    svc = SearchService(
        embedding_service=MagicMock(),
        client=MagicMock(),
        intent_models=MagicMock(),
    )
    with pytest.raises(ValueError, match="utterance must be a string"):
        svc.parse_utterance(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="utterance must be a string"):
        svc.parse_utterance(123)  # type: ignore[arg-type]


def test_init_loads_model_bundle_eagerly_and_parses_clinical_utterances(tmp_path: Any) -> None:
    from datetime import datetime

    from gosmart_graphite.nlu.intents import GraphiteReportQueryIntent, GraphiteStudyQueryIntent

    bundle_path = _ensure_test_nlu_bundle(tmp_path)
    svc = SearchService(
        embedding_service=MagicMock(),
        client=MagicMock(),
        nlu_model_path=bundle_path,
    )
    assert svc.nlu_ready is True
    assert svc.intent_models is not None
    assert set(svc.intent_models.names) >= {"find_study", "get_report"}

    study_res = svc.parse_utterance("Find a CT for patient 12345678")
    assert study_res.status == "matched"
    assert study_res.intent == "find_study"
    assert study_res.confidence >= study_res.threshold
    assert "find_study" in study_res.candidates
    assert "get_report" in study_res.candidates
    assert isinstance(study_res.data, GraphiteStudyQueryIntent)
    assert study_res.data.modality == "CT"
    assert study_res.data.patient_id == "12345678"

    report_res = svc.parse_utterance(
        "Get the MRI report from yesterday for patient 99887766",
        ref_date=datetime(2026, 11, 23),
    )
    assert report_res.status == "matched"
    assert report_res.intent == "get_report"
    assert isinstance(report_res.data, GraphiteReportQueryIntent)
    assert report_res.data.modality == "MR"
    assert report_res.data.patient_id == "99887766"
    assert report_res.data.study_date == "20261122"

    blank_res = svc.parse_utterance("   ")
    assert blank_res.status == "low_confidence"
    assert blank_res.confidence == 0.0

    unmatched_res = svc.parse_utterance("Hello world")
    assert unmatched_res.status == "low_confidence"


def test_init_resolves_relative_model_path_from_other_cwd(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    if not (repo_root / "nlu_models" / "intent_models.zip").is_file():
        pytest.skip("Repository nlu_models/intent_models.zip not present in this checkout")

    monkeypatch.chdir(tmp_path)
    svc = SearchService(
        embedding_service=MagicMock(),
        client=MagicMock(),
        nlu_model_path="./nlu_models/intent_models.zip",
    )
    assert svc.nlu_ready is True
    assert svc.nlu_model_path == (repo_root / "nlu_models" / "intent_models.zip").resolve()


def test_init_raises_on_explicit_missing_model_path_or_require_flag(tmp_path: Any) -> None:
    from unittest.mock import patch

    missing = tmp_path / "does_not_exist.zip"
    with pytest.raises(FileNotFoundError, match="Clinical NLU model bundle not found"):
        SearchService(
            embedding_service=MagicMock(),
            client=MagicMock(),
            nlu_model_path=missing,
        )

    fake_settings = MagicMock(NLU_MODEL_PATH=str(missing))
    with patch("gosmart_graphite.service.search_service.get_settings", return_value=fake_settings):
        with pytest.raises(FileNotFoundError, match="Clinical NLU model bundle not found"):
            SearchService(
                embedding_service=MagicMock(),
                client=MagicMock(),
                require_nlu_model=True,
            )


def test_init_graceful_degradation_when_default_bundle_missing(tmp_path: Any) -> None:
    from unittest.mock import patch

    missing = tmp_path / "missing_default.zip"
    fake_settings = MagicMock(NLU_MODEL_PATH=str(missing))
    with patch("gosmart_graphite.service.search_service.get_settings", return_value=fake_settings):
        svc = SearchService(embedding_service=MagicMock(), client=MagicMock())

    assert svc.nlu_ready is False
    assert svc.intent_models is None
    with pytest.raises(RuntimeError, match="Clinical NLU intent models are not loaded"):
        svc.parse_utterance("Find a CT for patient 12345678")


def test_nlu_relative_date_patterns_match_last_and_this_expressions() -> None:
    from datetime import datetime
    from pathlib import Path

    from gosmart_clinical_nlu import load_training_dir, train_intent_models
    from gosmart_clinical_nlu.intents.find_study import StudyQueryIntent
    from gosmart_clinical_nlu.intents.get_report import ReportQueryIntent

    repo_root = Path(__file__).resolve().parents[2]
    corpus = load_training_dir(repo_root / "training")
    models = train_intent_models(corpus)
    svc = SearchService(
        embedding_service=MagicMock(),
        client=MagicMock(),
        intent_models=models,
    )

    ref = datetime(2026, 11, 25, 12, 0, 0)  # Wednesday
    cases = [
        ("Get the CT report for patient 12345678 from last week", "get_report", "20261118"),
        ("Get the CT report for patient 12345678 from this week", "get_report", "20261125"),
        ("Show me the MRI report for mrn 98765432 from last month", "get_report", "20261025"),
        ("Show me the MRI report for mrn 98765432 from this month", "get_report", "20261125"),
        ("Print the report for patient 12345678 from last year", "get_report", "20251125"),
        ("Print the report for patient 12345678 from this year", "get_report", "20261125"),
        ("Open the CT report for patient 12345678 from last monday", "get_report", "20261123"),
        ("Open the CT report for patient 12345678 from this monday", "get_report", "20261130"),
        ("Get the report for patient 12345678 from last saturday", "get_report", "20261121"),
        ("Get the report for patient 12345678 from this sunday", "get_report", "20261129"),
        ("Find MRI scans for mrn 98765432 from last week", "find_study", "20261118"),
        ("Find MRI scans for mrn 98765432 from this week", "find_study", "20261125"),
        ("List CT studies for patient 12345678 from this month", "find_study", "20261125"),
        ("Find ultrasounds for mrn 98765432 from this year", "find_study", "20261125"),
        ("List CT studies for patient 12345678 from last sunday", "find_study", "20261122"),
        ("Find MRI studies for patient 12345678 from this saturday", "find_study", "20261128"),
    ]
    for utterance, expected_intent, expected_date in cases:
        res = svc.parse_utterance(utterance, ref_date=ref)
        assert res.status == "matched", f"Unexpected status for {utterance!r}: {res}"
        assert res.intent == expected_intent, f"Unexpected intent for {utterance!r}: {res}"
        assert isinstance(res.data, (ReportQueryIntent, StudyQueryIntent))
        assert res.data.study_date == expected_date, f"Unexpected study_date for {utterance!r}: {res.data}"


def test_normalize_accession_number() -> None:
    from gosmart_graphite.nlu import normalize_accession_number

    assert normalize_accession_number("accession 123456789") == "123456789"
    assert normalize_accession_number("Accession Number ACC-123456") == "ACC-123456"
    assert normalize_accession_number("acc no. 2026-001234") == "2026-001234"
    assert normalize_accession_number("accession #987654321") == "987654321"
    assert normalize_accession_number("accession: ACC_2026_01") == "ACC_2026_01"
    assert normalize_accession_number("acc# 123456789") == "123456789"
    assert normalize_accession_number("accession from") is None
    assert normalize_accession_number("accession ") is None
    assert normalize_accession_number("accession 12345678901234567") is None


def test_nlu_accession_number_and_patient_id_disambiguation() -> None:
    from pathlib import Path

    from gosmart_clinical_nlu import load_training_dir, train_intent_models

    from gosmart_graphite.nlu import (
        GraphiteReportQueryIntent,
        GraphiteStudyQueryIntent,
        register_graphite_intents,
    )

    register_graphite_intents()
    repo_root = Path(__file__).resolve().parents[2]
    corpus = load_training_dir(repo_root / "training")
    models = train_intent_models(corpus)
    svc = SearchService(
        embedding_service=MagicMock(),
        client=MagicMock(),
        intent_models=models,
    )

    accession_cases = [
        ("Find accession 123456789", "find_study", "123456789", None),
        ("Get accession 123456789", "find_study", "123456789", None),
        ("Find accession number ACC-123456", "find_study", "ACC-123456", None),
        ("Get accession 2026-001234", "find_study", "2026-001234", None),
        ("Find accession 2026-11-001234", "find_study", "2026-11-001234", None),
        ("Look up accession #987654321", "find_study", "987654321", None),
        ("Get the radiology report for accession 123456789", "get_report", "123456789", None),
        ("Show me the report for accession no. ACC12345", "get_report", "ACC12345", None),
        ("Get the radiology report for 123456789", "get_report", None, "123456789"),
        ("Find a CT for 123456789", "find_study", None, "123456789"),
        ("Get the radiology report for patient 12345678 accession 987654321", "get_report", "987654321", "12345678"),
    ]
    for utterance, expected_intent, expected_acc, expected_pid in accession_cases:
        res = svc.parse_utterance(utterance)
        assert res.status == "matched", f"Unexpected status for {utterance!r}: {res}"
        assert res.intent == expected_intent, f"Unexpected intent for {utterance!r}: {res}"
        assert isinstance(res.data, (GraphiteReportQueryIntent, GraphiteStudyQueryIntent))
        assert res.data.accession_number == expected_acc, f"Unexpected accession for {utterance!r}: {res.data}"
        assert res.data.patient_id == expected_pid, f"Unexpected patient_id for {utterance!r}: {res.data}"
        if utterance in ("Find accession 123456789", "Get accession 123456789"):
            assert res.data.modality is None
            assert res.data.study_date is None

    for ood in ("Hello world", "What is the weather?"):
        res = svc.parse_utterance(ood)
        assert res.status == "low_confidence", f"Expected low_confidence for {ood!r}: {res}"
