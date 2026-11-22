"""Modular unit tests for Cypher schema script consolidation, loader, and GraphService initialization."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gosmart_graphite.db.schema_loader import (
    SCHEMA_SCRIPT_FILES,
    SchemaInitializationError,
    extract_error_location,
    load_schema_statements,
    parse_cypher_script,
)
from gosmart_graphite.model.fhir import FHIR_ALL_SCHEMA_DDL
from gosmart_graphite.service.graph_service import GraphService
from gosmart_graphite.service.group_service import GroupService

_DB_DIR = Path(__file__).resolve().parents[2] / "src" / "gosmart_graphite" / "db"


def test_schema_manifest_matches_committed_cypher_files() -> None:
    """Guardrail ensuring SCHEMA_SCRIPT_FILES exactly matches committed .cypher files in db/."""
    assert SCHEMA_SCRIPT_FILES == (
        "system_schema.cypher",
        "config_schema.cypher",
        "dicom_schema.cypher",
        "fhir_schema.cypher",
        "embedding_schema.cypher",
    )
    committed_files = sorted(p.name for p in _DB_DIR.glob("*.cypher"))
    assert committed_files == sorted(SCHEMA_SCRIPT_FILES)
    for filename in SCHEMA_SCRIPT_FILES:
        assert (_DB_DIR / filename).is_file()

    # embedding_schema.cypher is intentionally empty for now
    assert (_DB_DIR / "embedding_schema.cypher").read_text(encoding="utf-8") == ""


def test_load_schema_statements_complete_coverage_and_ordering() -> None:
    """Verify all System, Config, DICOM, and FHIR statements load in deterministic manifest order."""
    statements = load_schema_statements()
    by_file: dict[str, list[str]] = {name: [] for name in SCHEMA_SCRIPT_FILES}
    for stmt in statements:
        by_file[stmt.source_file].append(stmt.statement)

    assert len(by_file["system_schema.cypher"]) == 10
    assert len(by_file["config_schema.cypher"]) == 12
    assert len(by_file["dicom_schema.cypher"]) == 8
    assert by_file["fhir_schema.cypher"] == list(FHIR_ALL_SCHEMA_DDL)
    assert by_file["embedding_schema.cypher"] == []

    # Spot-check domain placement
    assert any("FOR (g:Group) REQUIRE g.code IS UNIQUE" in s for s in by_file["system_schema.cypher"])
    assert any("FOR ()-[r:BELONGS_TO]-() ON (r.id)" in s for s in by_file["system_schema.cypher"])
    assert any("FOR ()-[r:SERVES]-() ON (r.id)" in s for s in by_file["config_schema.cypher"])
    assert any("FOR ()-[r:NOT_SERVES]-() ON (r.id)" in s for s in by_file["config_schema.cypher"])
    assert any("FOR (l:LLM_Config) REQUIRE l.id IS UNIQUE" in s for s in by_file["config_schema.cypher"])
    assert any("FOR (i:Instance) REQUIRE i.sop_instance_uid IS UNIQUE" in s for s in by_file["dicom_schema.cypher"])


def test_parse_cypher_script_handles_multiline_comments_quotes_and_backticks() -> None:
    """Verify parser preserves accurate 1-based line ranges across comments, quotes, and multi-line DDL."""
    script = "\n".join(
        [
            "// Line 1 header comment",
            "/* Block comment",
            "   spanning line 2 to 3 */",
            "",
            "CREATE INDEX idx_class IF NOT EXISTS FOR (n:Encounter) ON (n.`class`);",
            "",
            "CREATE VECTOR INDEX embed_idx IF NOT EXISTS",
            "FOR (e:Embedding)",
            "ON (e.vector) // inline comment on line 9",
            "OPTIONS {",
            "  indexConfig: {",
            "    `vector.similarity_function`: 'cosine; // not a split'",
            "  }",
            "};",
            "// Trailing comment without statement",
        ]
    )

    parsed = parse_cypher_script(script, source_file="sample.cypher")
    assert len(parsed) == 2

    first = parsed[0]
    assert first.source_file == "sample.cypher"
    assert first.statement_index == 1
    assert first.start_line == 5
    assert first.end_line == 5
    assert first.line_range_label == "line 5"
    assert first.statement == "CREATE INDEX idx_class IF NOT EXISTS FOR (n:Encounter) ON (n.`class`)"

    second = parsed[1]
    assert second.statement_index == 2
    assert second.start_line == 7
    assert second.end_line == 14
    assert second.line_range_label == "lines 7-14"
    assert "'cosine; // not a split'" in second.statement
    # Line alignment preserved inside statement: OPTIONS { is on relative line 4 -> file line 10
    stmt_lines = second.statement.splitlines()
    assert stmt_lines[3].strip() == "OPTIONS {"


def test_parse_cypher_script_empty_and_comment_only() -> None:
    assert parse_cypher_script("", source_file="empty.cypher") == []
    assert parse_cypher_script("   \n// just a comment\n/* another */\n", source_file="comments.cypher") == []


@pytest.mark.parametrize(
    ("content", "expected_line", "expected_phrase"),
    [
        ("CREATE INDEX a;\n/* unterminated block\nstill open", 2, "Unterminated block comment"),
        ("CREATE INDEX a;\n\nMATCH (n {name: 'oops})", 3, "Unterminated string literal"),
        ("CREATE INDEX a;\nMATCH (n:`unclosed)", 2, "Unterminated backtick identifier"),
    ],
)
def test_parse_cypher_script_lexical_errors_report_exact_line(
    content: str, expected_line: int, expected_phrase: str
) -> None:
    with pytest.raises(SchemaInitializationError) as exc_info:
        parse_cypher_script(content, source_file="broken.cypher")

    err = exc_info.value
    assert err.script_file == "broken.cypher"
    assert err.error_line == expected_line
    assert expected_phrase in str(err)
    assert f"line {expected_line}" in str(err)


def test_load_schema_statements_missing_file_raises_hard_error(tmp_path: Path) -> None:
    with pytest.raises(SchemaInitializationError) as exc_info:
        load_schema_statements(script_files=("does_not_exist.cypher",), schema_dir=tmp_path)

    err = exc_info.value
    assert err.script_file == "does_not_exist.cypher"
    assert "does_not_exist.cypher" in str(err)


@pytest.mark.parametrize("bad_name", ["../outside.cypher", "subdir/nested.cypher", ""])
def test_load_schema_statements_rejects_path_traversal(tmp_path: Path, bad_name: str) -> None:
    with pytest.raises(SchemaInitializationError):
        load_schema_statements(script_files=(bad_name,), schema_dir=tmp_path)


def test_extract_error_location_maps_neo4j_relative_line_to_file_line() -> None:
    script = "\n".join(
        [
            "// header",
            "// line 2",
            "CREATE VECTOR INDEX bad_idx",
            "FOR (e:Embedding)",
            "ON (e.vector)",
            "OPTIONS { bad_key: 1 };",
        ]
    )
    stmt = parse_cypher_script(script, source_file="embedding_schema.cypher")[0]
    assert stmt.start_line == 3
    assert stmt.end_line == 6

    fake_neo4j_err = RuntimeError("Invalid input 'bad_key': expected ... (line 4, column 11 (offset: 70))")
    abs_line, col = extract_error_location(stmt, fake_neo4j_err)
    assert abs_line == 6  # start_line (3) + (4 - 1)
    assert col == 11


@pytest.mark.anyio
async def test_init_schema_constraints_fails_hard_with_exact_file_and_line(tmp_path: Path) -> None:
    schema_file = tmp_path / "broken_schema.cypher"
    schema_file.write_text(
        "\n".join(
            [
                "// Comment on line 1",
                "CREATE CONSTRAINT ok_1 IF NOT EXISTS FOR (a:Alpha) REQUIRE a.id IS UNIQUE;",
                "",
                "CREATE VECTOR INDEX bad_vec IF NOT EXISTS",
                "FOR (e:Embedding)",
                "ON (e.vector)",
                "OPTIONS { invalidOption: true };",
                "CREATE CONSTRAINT never_reached IF NOT EXISTS FOR (b:Beta) REQUIRE b.id IS UNIQUE;",
            ]
        ),
        encoding="utf-8",
    )

    mock_client = MagicMock()
    mock_client.settings = SimpleNamespace(DB_CONNECTION_URI="bolt://localhost:7687", DEV_MODE=False)
    mock_client.verify_connectivity = AsyncMock(return_value=True)

    async def _exec_side_effect(query: str):
        if "bad_vec" in query:
            raise RuntimeError("Neo.ClientError.Statement.SyntaxError: Invalid input (line 4, column 11 (offset: 80))")
        return []

    mock_client.execute_query = AsyncMock(side_effect=_exec_side_effect)
    svc = GraphService(
        client=mock_client,
        schema_dir=tmp_path,
        schema_scripts=("broken_schema.cypher",),
    )

    with pytest.raises(SchemaInitializationError) as exc_info:
        await svc.init_schema_constraints(force=True)

    err = exc_info.value
    assert err.script_file == "broken_schema.cypher"
    assert err.start_line == 4
    assert err.end_line == 7
    assert err.error_line == 7
    assert err.error_column == 11
    assert err.statement_index == 2
    assert "broken_schema.cypher" in str(err)
    assert "line 7, column 11" in str(err)
    assert "lines 4-7" in str(err)
    # Must stop immediately and NOT execute subsequent statements or mark initialized
    assert mock_client.execute_query.await_count == 2
    assert svc._constraints_initialized is False


@pytest.mark.anyio
async def test_init_schema_constraints_dev_mode_relaxes_offline_neo4j_but_still_validates_files(
    tmp_path: Path,
) -> None:
    # 1. Valid scripts + DEV_MODE=True + Neo4j offline => graceful warning & skip
    (tmp_path / "valid.cypher").write_text(
        "CREATE CONSTRAINT ok IF NOT EXISTS FOR (a:Alpha) REQUIRE a.id IS UNIQUE;\n",
        encoding="utf-8",
    )
    mock_client = MagicMock()
    mock_client.settings = SimpleNamespace(DB_CONNECTION_URI="bolt://localhost:7687", DEV_MODE=True)
    mock_client.verify_connectivity = AsyncMock(return_value=False)
    mock_client.execute_query = AsyncMock(return_value=[])

    svc = GraphService(client=mock_client, schema_dir=tmp_path, schema_scripts=("valid.cypher",))
    await svc.init_schema_constraints(force=True)
    assert mock_client.verify_connectivity.await_count == 1
    assert mock_client.execute_query.await_count == 0
    assert svc._constraints_initialized is False

    # 2. Missing script + DEV_MODE=True + Neo4j offline => STILL a hard failure!
    svc_missing = GraphService(client=mock_client, schema_dir=tmp_path, schema_scripts=("missing.cypher",))
    with pytest.raises(SchemaInitializationError) as exc_info:
        await svc_missing.init_schema_constraints(force=True)
    assert exc_info.value.script_file == "missing.cypher"


@pytest.mark.anyio
async def test_init_schema_constraints_prod_mode_retries_connectivity_until_recovered(tmp_path: Path) -> None:
    (tmp_path / "valid.cypher").write_text(
        "CREATE CONSTRAINT ok IF NOT EXISTS FOR (a:Alpha) REQUIRE a.id IS UNIQUE;\n",
        encoding="utf-8",
    )
    mock_client = MagicMock()
    mock_client.settings = SimpleNamespace(
        DB_CONNECTION_URI="bolt://neo4j.prod:7687",
        DEV_MODE=False,
        DB_CONNECT_RETRY_TIMEOUT_SEC=5,
        DB_CONNECT_RETRY_INITIAL_DELAY_SEC=0.005,
        DB_CONNECT_RETRY_MAX_DELAY_SEC=0.01,
    )
    # Fail twice (transient outage), then recover on 3rd attempt
    mock_client.verify_connectivity = AsyncMock(side_effect=[False, False, True])
    mock_client.execute_query = AsyncMock(return_value=[])

    svc = GraphService(client=mock_client, schema_dir=tmp_path, schema_scripts=("valid.cypher",))
    with patch.object(GroupService, "ensure_lost_and_found_group", new=AsyncMock()):
        await svc.init_schema_constraints(force=True)

    assert mock_client.verify_connectivity.await_count == 3
    assert mock_client.execute_query.await_count == 1
    assert svc._constraints_initialized is True


@pytest.mark.anyio
async def test_init_schema_constraints_prod_mode_raises_after_retry_timeout(tmp_path: Path) -> None:
    (tmp_path / "valid.cypher").write_text(
        "CREATE CONSTRAINT ok IF NOT EXISTS FOR (a:Alpha) REQUIRE a.id IS UNIQUE;\n",
        encoding="utf-8",
    )
    mock_client = MagicMock()
    mock_client.settings = SimpleNamespace(
        DB_CONNECTION_URI="bolt://neo4j.prod:7687",
        DEV_MODE=False,
        DB_CONNECT_RETRY_TIMEOUT_SEC=0.03,
        DB_CONNECT_RETRY_INITIAL_DELAY_SEC=0.005,
        DB_CONNECT_RETRY_MAX_DELAY_SEC=0.01,
    )
    mock_client.verify_connectivity = AsyncMock(return_value=False)
    mock_client.execute_query = AsyncMock(return_value=[])

    svc = GraphService(client=mock_client, schema_dir=tmp_path, schema_scripts=("valid.cypher",))
    with pytest.raises(SchemaInitializationError) as exc_info:
        await svc.init_schema_constraints(force=True)

    assert "remained unreachable" in str(exc_info.value)
    assert mock_client.verify_connectivity.await_count >= 2
    assert mock_client.execute_query.await_count == 0
    assert svc._constraints_initialized is False


@pytest.mark.anyio
async def test_main_lifespan_aborts_startup_on_schema_initialization_error() -> None:
    """Verify FastAPI lifespan never swallows SchemaInitializationError (even when DEV_MODE=True)."""
    from gosmart_graphite import main as main_module

    fake_settings = SimpleNamespace(
        APP_NAME="TestGraphite",
        APP_VERSION="0.1.0",
        DEV_MODE=True,
        LOG_ROTATION_DAYS=7,
        STORAGE_ROOT_URI="file:///tmp/test",
        DB_CONNECTION_URI="bolt://localhost:7687",
    )

    with (
        patch.object(main_module, "logo_print"),
        patch.object(main_module, "setup_logging"),
        patch.object(main_module, "get_settings", return_value=fake_settings),
        patch.object(main_module.neo4j_client, "connect", new=AsyncMock()),
        patch.object(
            main_module.graph_service,
            "init_schema_constraints",
            new=AsyncMock(
                side_effect=SchemaInitializationError("Missing schema file", script_file="config_schema.cypher")
            ),
        ),
    ):
        with pytest.raises(SchemaInitializationError, match="Missing schema file"):
            async with main_module.lifespan(MagicMock()):
                pass
