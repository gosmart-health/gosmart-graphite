"""Configuration CLI commands for Gosmart.Health GRAPHITE."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from gosmart_graphite.core.logging import setup_logging
from gosmart_graphite.db.neo4j import neo4j_client
from gosmart_graphite.model.llm import LLMConfigCreateRequest
from gosmart_graphite.service.llm_config_service import llm_config_service

configure_app = typer.Typer(
    name="configure",
    help="Configure runtime settings persisted in Neo4j.",
    add_completion=False,
)
console = Console()


@configure_app.command("llm-config")
def configure_llm_config(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Path to a JSON file containing a single LLM_Config object (arrays are rejected).",
    ),
    assign: Optional[str] = typer.Option(
        None,
        "--assign",
        "-a",
        help="Target Group ID, name, or code to sponsor this LLM_Config (defaults to 'GoSmart Health').",
    ),
    acted_by: str = typer.Option(
        "cli:configure",
        "--acted-by",
        help="Audit actor identifier recorded on the Neo4j node/relationship.",
    ),
    test_id: Optional[str] = typer.Option(
        None,
        "--test-id",
        help="Optional ephemeral test identifier.",
    ),
) -> None:
    """Register or overwrite a purpose-tagged LLM_Config node from a JSON file and assign it to a Group."""
    setup_logging()

    file_path = file.expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        console.print(f"[red]Error: Configuration file not found at {file_path}[/red]")
        raise typer.Exit(code=1)

    try:
        raw_text = file_path.read_text(encoding="utf-8")
        raw_payload = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Error: Failed to parse JSON configuration file '{file_path}': {exc}[/red]")
        raise typer.Exit(code=1)

    if not isinstance(raw_payload, dict):
        console.print(
            "[red]Error: --file must contain a single JSON object representing one LLM_Config "
            "(JSON arrays or scalar values are not supported).[/red]"
        )
        raise typer.Exit(code=1)

    try:
        req = LLMConfigCreateRequest(**raw_payload)
    except (ValidationError, ValueError) as exc:
        console.print(f"[red]Error: Invalid LLM_Config payload: {exc}[/red]")
        raise typer.Exit(code=1)

    async def _run() -> dict[str, Any]:
        try:
            return await llm_config_service.upsert_llm_config(
                config_data=req,
                assign_group=assign,
                acted_by=acted_by,
                test_id=test_id,
            )
        finally:
            await neo4j_client.close()

    try:
        saved = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]Error: Failed to persist LLM_Config in Neo4j: {exc}[/red]")
        raise typer.Exit(code=1)

    groups_display = ", ".join(
        f"{g.get('name', g.get('id', ''))} ({g.get('id', '')})" for g in (saved.get("groups") or [])
    )
    table = Table(title=f"LLM Configuration Registered ({saved.get('action', 'CREATE')})")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("ID", str(saved.get("id", "")))
    table.add_row("Tag", str(saved.get("tag", "")))
    table.add_row("Provider", str(saved.get("provider", "")))
    table.add_row("Model", str(saved.get("model", "")))
    table.add_row("URL", str(saved.get("url") or "-"))
    table.add_row("Dimensions", str(saved.get("dimensions") if saved.get("dimensions") is not None else "-"))
    table.add_row(
        "Dimensions Truncated",
        str(saved.get("dimensions_truncated") if saved.get("dimensions_truncated") is not None else "-"),
    )
    table.add_row("Location", str(saved.get("location", "us-central1")))
    table.add_row("Project ID", str(saved.get("project_id") or "-"))
    table.add_row(
        "API Key",
        f"{saved.get('api_key') or '-'} ({saved.get('api_key_source', 'none')})",
    )
    table.add_row("Sponsoring Groups", groups_display or "-")
    if saved.get("test_id"):
        table.add_row("Test ID", str(saved.get("test_id")))

    console.print(table)


if __name__ == "__main__":
    configure_app()
