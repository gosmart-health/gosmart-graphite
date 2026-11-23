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
from gosmart_graphite.model.dicom_config import DICOMConfigurationCreateRequest
from gosmart_graphite.model.fhir_config import FHIRConfigurationCreateRequest
from gosmart_graphite.model.llm_config import LLMConfigCreateRequest
from gosmart_graphite.service.fhir_config_service import fhir_config_service
from gosmart_graphite.service.graph_service import graph_service
from gosmart_graphite.service.group_service import DEFAULT_GROUP_NAME, group_service
from gosmart_graphite.service.llm_config_service import llm_config_service
from gosmart_graphite.service.system_service import system_service

configure_app = typer.Typer(
    name="configure",
    help="Configure runtime settings persisted in Neo4j.",
    add_completion=False,
)
console = Console()


async def _ensure_cli_db_ready(
    assign_group: Optional[str],
    acted_by: str,
    test_id: Optional[str],
) -> None:
    """Ensure Neo4j schema constraints/indexes exist and bootstrap default Group on fresh databases."""
    await graph_service.init_schema_constraints()
    clean_assign = assign_group.strip() if isinstance(assign_group, str) and assign_group.strip() else None
    if clean_assign is None or clean_assign == DEFAULT_GROUP_NAME:
        try:
            await group_service.resolve_target_group(identifier=clean_assign, test_id=test_id)
        except RuntimeError as exc:
            if "not found" in str(exc).lower():
                await system_service.bootstrap_system(acted_by=acted_by, test_id=test_id)
            else:
                raise


def _load_single_json_object(file: Path, entity_label: str) -> dict[str, Any]:
    """Read and validate that a configuration file contains a single JSON object."""
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
            f"[red]Error: --file must contain a single JSON object representing one {entity_label} "
            "(JSON arrays or scalar values are not supported).[/red]"
        )
        raise typer.Exit(code=1)

    return raw_payload


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

    raw_payload = _load_single_json_object(file, "LLM_Config")

    try:
        req = LLMConfigCreateRequest(**raw_payload)
    except (ValidationError, ValueError) as exc:
        console.print(f"[red]Error: Invalid LLM_Config payload: {exc}[/red]")
        raise typer.Exit(code=1)

    async def _run() -> dict[str, Any]:
        try:
            await _ensure_cli_db_ready(
                assign_group=assign if assign is not None else req.assign_group,
                acted_by=acted_by,
                test_id=test_id if test_id is not None else req.test_id,
            )
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


@configure_app.command("dicom-config")
def configure_dicom_config(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Path to a JSON file containing a single DICOMConfiguration object (arrays are rejected).",
    ),
    assign: Optional[str] = typer.Option(
        None,
        "--assign",
        "-a",
        help="Target Group ID, name, or code served by this DICOMConfiguration (defaults to 'GoSmart Health').",
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
    """Register a DICOMConfiguration node from a JSON file and assign it to a Group."""
    setup_logging()

    raw_payload = _load_single_json_object(file, "DICOMConfiguration")

    try:
        req = DICOMConfigurationCreateRequest(**raw_payload)
    except (ValidationError, ValueError) as exc:
        console.print(f"[red]Error: Invalid DICOMConfiguration payload: {exc}[/red]")
        raise typer.Exit(code=1)

    if assign is not None:
        req.group_id = assign.strip() or None
    if test_id is not None:
        req.test_id = test_id.strip() or None

    async def _run() -> dict[str, Any]:
        try:
            await _ensure_cli_db_ready(
                assign_group=assign if assign is not None else req.group_id,
                acted_by=acted_by,
                test_id=req.test_id,
            )
            return await system_service.create_configuration(
                config_data=req,
                assign_group=assign,
                acted_by=acted_by,
            )
        finally:
            await neo4j_client.close()

    try:
        saved = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]Error: Failed to persist DICOMConfiguration in Neo4j: {exc}[/red]")
        raise typer.Exit(code=1)

    groups_display = ", ".join(
        f"{g.get('name', g.get('id', ''))} ({g.get('id', '')})" for g in (saved.get("groups") or [])
    )
    table = Table(title=f"DICOM Configuration Registered ({saved.get('action', 'CREATE')})")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("ID", str(saved.get("id", "")))
    table.add_row("AE Title", str(saved.get("ae_title", "")))
    table.add_row("Roles", ", ".join(saved.get("roles") or []) or "-")
    table.add_row("Host", str(saved.get("host", "")))
    table.add_row("Port", str(saved.get("port", "")))
    table.add_row("Local Server", str(saved.get("is_local", False)))
    table.add_row("Promiscuous", str(saved.get("is_promiscuous", False)))
    table.add_row("TLS Enabled", str(saved.get("tls_enabled", False)))
    table.add_row("DICOM Services", ", ".join(saved.get("dicom_services") or []) or "-")
    table.add_row("Transfer Syntaxes", ", ".join(saved.get("transfer_syntaxes") or []) or "-")
    table.add_row("DICOMweb Enabled", str(saved.get("dicomweb_enabled", True)))
    table.add_row("DICOMweb Prefix", str(saved.get("dicomweb_url_prefix") or "-"))
    table.add_row("Storage Root URI", str(saved.get("storage_root_uri") or "-"))
    table.add_row("Description", str(saved.get("description") or "-"))
    table.add_row("Group ID", str(saved.get("group_id") or "-"))
    table.add_row("Sponsoring Groups", groups_display or str(saved.get("group_id") or "-"))
    if saved.get("test_id"):
        table.add_row("Test ID", str(saved.get("test_id")))

    console.print(table)


@configure_app.command("fhir-config")
def configure_fhir_config(
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Path to a JSON file containing a single FHIRConfiguration object (arrays are rejected).",
    ),
    assign: Optional[str] = typer.Option(
        None,
        "--assign",
        "-a",
        help="Target Group ID, name, or code to associate with this FHIRConfiguration (defaults to 'GoSmart Health').",
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
    """Register a FHIRConfiguration node from a JSON file and assign it to a Group."""
    setup_logging()

    raw_payload = _load_single_json_object(file, "FHIRConfiguration")

    try:
        req = FHIRConfigurationCreateRequest(**raw_payload)
    except (ValidationError, ValueError) as exc:
        console.print(f"[red]Error: Invalid FHIRConfiguration payload: {exc}[/red]")
        raise typer.Exit(code=1)

    async def _run() -> dict[str, Any]:
        try:
            await _ensure_cli_db_ready(
                assign_group=assign if assign is not None else req.group_id,
                acted_by=acted_by,
                test_id=test_id if test_id is not None else req.test_id,
            )
            return await fhir_config_service.create_fhir_config(
                config_data=req,
                assign_group_id=assign,
                acted_by=acted_by,
                test_id=test_id,
            )
        finally:
            await neo4j_client.close()

    try:
        saved = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]Error: Failed to persist FHIRConfiguration in Neo4j: {exc}[/red]")
        raise typer.Exit(code=1)

    groups_display = ", ".join(
        f"{g.get('name', g.get('id', ''))} ({g.get('id', '')})" for g in (saved.get("groups") or [])
    )
    table = Table(title=f"FHIR Configuration Registered ({saved.get('action', 'CREATE')})")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("ID", str(saved.get("id", "")))
    table.add_row("URL", str(saved.get("url", "")))
    table.add_row("Accession System", str(saved.get("accession_system") or "-"))
    table.add_row("Patient ID System", str(saved.get("patient_id_system") or "-"))
    table.add_row("Poll Interval (s)", str(saved.get("poll_sec", 120)))
    table.add_row("Sponsoring Groups", groups_display or "-")
    if saved.get("test_id"):
        table.add_row("Test ID", str(saved.get("test_id")))

    console.print(table)


if __name__ == "__main__":
    configure_app()
