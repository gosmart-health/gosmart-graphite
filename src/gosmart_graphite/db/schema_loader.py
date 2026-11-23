"""Explicit manifest loader and line-accurate parser for Neo4j Cypher schema scripts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from gosmart_graphite.core.logging import get_logger

logger = get_logger("gosmart_gs.schema")

# Explicit, immutable allowlist of schema DDL scripts executed in deterministic order.
# Using an explicit manifest instead of directory globbing prevents arbitrary Cypher
# script injection and guarantees fail-closed detection of missing schema assets.
SCHEMA_SCRIPT_FILES: tuple[str, ...] = (
    "system_schema.cypher",
    "config_schema.cypher",
    "dicom_schema.cypher",
    "fhir_schema.cypher",
    "embedding_schema.cypher",
)

_DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parent
_NEO4J_LINE_COL_RE = re.compile(r"\(line\s+(\d+),\s*column\s+(\d+)")


class SchemaInitializationError(RuntimeError):
    """Non-negotiable fatal error raised when loading, parsing, or executing schema scripts fails."""

    def __init__(
        self,
        message: str,
        *,
        script_file: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        error_line: Optional[int] = None,
        error_column: Optional[int] = None,
        statement_index: Optional[int] = None,
        statement: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.script_file = script_file
        self.start_line = start_line
        self.end_line = end_line
        self.error_line = error_line
        self.error_column = error_column
        self.statement_index = statement_index
        self.statement = statement
        self.cause = cause


@dataclass(frozen=True)
class CypherStatement:
    """Single parsed Cypher statement with 1-based source file line coordinates."""

    source_file: str
    statement_index: int
    start_line: int
    end_line: int
    statement: str

    @property
    def line_range_label(self) -> str:
        """Human-readable 1-based line or line-range string for triage logs."""
        if self.start_line == self.end_line:
            return f"line {self.start_line}"
        return f"lines {self.start_line}-{self.end_line}"


def extract_error_location(
    stmt: CypherStatement,
    exc: BaseException,
) -> tuple[int, Optional[int]]:
    """Resolve the most specific 1-based file line (and optional column) for a Neo4j statement failure."""
    texts = [str(exc)]
    exc_msg = getattr(exc, "message", None)
    if isinstance(exc_msg, str) and exc_msg:
        texts.insert(0, exc_msg)

    for text in texts:
        match = _NEO4J_LINE_COL_RE.search(text)
        if match:
            rel_line = int(match.group(1))
            col = int(match.group(2))
            abs_line = stmt.start_line + max(rel_line - 1, 0)
            return abs_line, col

    return stmt.start_line, None


def parse_cypher_script(content: str, source_file: str) -> list[CypherStatement]:
    """Parse a `.cypher` script into individual statements while preserving exact 1-based line numbers.

    Handles single-line (`//`) and block (`/* ... */`) comments, single/double-quoted string
    literals with backslash escapes, backtick-quoted identifiers (`` `...` ``), multi-line
    statements, and empty/comment-only scripts.
    """
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    length = len(text)
    idx = 0
    line = 1

    statements: list[CypherStatement] = []
    buf: list[str] = []
    start_line: Optional[int] = None
    last_non_ws_line: Optional[int] = None

    def _flush_statement() -> None:
        nonlocal buf, start_line, last_non_ws_line
        if start_line is not None:
            stmt_text = "".join(buf).strip()
            if stmt_text:
                statements.append(
                    CypherStatement(
                        source_file=source_file,
                        statement_index=len(statements) + 1,
                        start_line=start_line,
                        end_line=last_non_ws_line or start_line,
                        statement=stmt_text,
                    )
                )
        buf = []
        start_line = None
        last_non_ws_line = None

    while idx < length:
        ch = text[idx]
        nxt = text[idx + 1] if idx + 1 < length else ""

        # Single-line comment: // ... \n
        if ch == "/" and nxt == "/":
            idx += 2
            while idx < length and text[idx] != "\n":
                idx += 1
            continue

        # Block comment: /* ... */
        if ch == "/" and nxt == "*":
            comment_start_line = line
            idx += 2
            newline_count = 0
            closed = False
            while idx < length:
                if text[idx] == "*" and idx + 1 < length and text[idx + 1] == "/":
                    idx += 2
                    closed = True
                    break
                if text[idx] == "\n":
                    line += 1
                    newline_count += 1
                idx += 1
            if not closed:
                msg = (
                    f"Unterminated block comment in Cypher script '{source_file}' starting at line {comment_start_line}"
                )
                logger.error(
                    "schema_script_parse_failed",
                    script_file=source_file,
                    error_line=comment_start_line,
                    error=msg,
                )
                raise SchemaInitializationError(
                    msg,
                    script_file=source_file,
                    start_line=comment_start_line,
                    end_line=line,
                    error_line=comment_start_line,
                )
            if start_line is not None:
                buf.append("\n" * newline_count if newline_count > 0 else " ")
            continue

        # Single- or double-quoted string literal
        if ch in ("'", '"'):
            quote = ch
            quote_start_line = line
            if start_line is None:
                start_line = line
            buf.append(ch)
            idx += 1
            closed = False
            while idx < length:
                cur = text[idx]
                buf.append(cur)
                if cur == "\\" and idx + 1 < length:
                    idx += 1
                    escaped = text[idx]
                    buf.append(escaped)
                    if escaped == "\n":
                        line += 1
                    idx += 1
                    continue
                if cur == "\n":
                    line += 1
                elif cur == quote:
                    idx += 1
                    closed = True
                    break
                idx += 1
            if not closed:
                msg = (
                    f"Unterminated string literal in Cypher script '{source_file}' starting at line {quote_start_line}"
                )
                logger.error(
                    "schema_script_parse_failed",
                    script_file=source_file,
                    error_line=quote_start_line,
                    error=msg,
                )
                raise SchemaInitializationError(
                    msg,
                    script_file=source_file,
                    start_line=quote_start_line,
                    end_line=line,
                    error_line=quote_start_line,
                )
            last_non_ws_line = line
            continue

        # Backtick-quoted identifier: `...` (escaped backtick is ``)
        if ch == "`":
            tick_start_line = line
            if start_line is None:
                start_line = line
            buf.append(ch)
            idx += 1
            closed = False
            while idx < length:
                cur = text[idx]
                buf.append(cur)
                if cur == "`":
                    if idx + 1 < length and text[idx + 1] == "`":
                        idx += 1
                        buf.append(text[idx])
                        idx += 1
                        continue
                    idx += 1
                    closed = True
                    break
                if cur == "\n":
                    line += 1
                idx += 1
            if not closed:
                msg = (
                    f"Unterminated backtick identifier in Cypher script '{source_file}' "
                    f"starting at line {tick_start_line}"
                )
                logger.error(
                    "schema_script_parse_failed",
                    script_file=source_file,
                    error_line=tick_start_line,
                    error=msg,
                )
                raise SchemaInitializationError(
                    msg,
                    script_file=source_file,
                    start_line=tick_start_line,
                    end_line=line,
                    error_line=tick_start_line,
                )
            last_non_ws_line = line
            continue

        # Statement terminator
        if ch == ";":
            _flush_statement()
            idx += 1
            continue

        # Regular character
        if ch.isspace():
            if start_line is not None:
                buf.append(ch)
            if ch == "\n":
                line += 1
        else:
            if start_line is None:
                start_line = line
            last_non_ws_line = line
            buf.append(ch)
        idx += 1

    # Flush trailing statement if script omits final semicolon
    _flush_statement()
    return statements


def _resolve_script_path(schema_dir: Path, filename: str) -> Path:
    """Resolve and strictly confine a schema script filename within schema_dir."""
    if not filename or Path(filename).name != filename or ".." in filename:
        msg = f"Invalid Cypher schema script filename (must be a plain filename without path traversal): '{filename}'"
        logger.error("schema_script_invalid_name", script_file=filename, schema_dir=str(schema_dir))
        raise SchemaInitializationError(msg, script_file=filename)

    resolved_dir = schema_dir.resolve()
    candidate = (resolved_dir / filename).resolve()
    try:
        candidate.relative_to(resolved_dir)
    except ValueError as exc:
        msg = f"Cypher schema script '{filename}' resolves outside schema directory '{resolved_dir}'"
        logger.error("schema_script_path_escape", script_file=filename, resolved_path=str(candidate))
        raise SchemaInitializationError(msg, script_file=filename, cause=exc) from exc

    return candidate


def load_schema_statements(
    script_files: Sequence[str] = SCHEMA_SCRIPT_FILES,
    schema_dir: Optional[Path] = None,
) -> list[CypherStatement]:
    """Load and parse all allowlisted `.cypher` schema scripts in deterministic order.

    Raises `SchemaInitializationError` immediately if any script in the manifest is missing,
    unreadable, outside the schema directory, or contains lexical/parse errors.
    """
    base_dir = (schema_dir or _DEFAULT_SCHEMA_DIR).resolve()
    all_statements: list[CypherStatement] = []

    for filename in script_files:
        script_path = _resolve_script_path(base_dir, filename)
        if not script_path.exists() or not script_path.is_file():
            msg = f"Required Cypher schema script not found: '{filename}' (expected at {script_path})"
            logger.error("schema_script_missing", script_file=filename, expected_path=str(script_path))
            raise SchemaInitializationError(msg, script_file=filename)

        try:
            content = script_path.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"Failed to read Cypher schema script '{filename}' at {script_path}: {exc}"
            logger.error("schema_script_read_failed", script_file=filename, path=str(script_path), error=str(exc))
            raise SchemaInitializationError(msg, script_file=filename, cause=exc) from exc

        parsed = parse_cypher_script(content, source_file=filename)
        logger.debug("schema_script_loaded", script_file=filename, statement_count=len(parsed))
        all_statements.extend(parsed)

    return all_statements
