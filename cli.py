import asyncio
import json
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer
import uvicorn
from actor.dicom_actor import get_dicom_actor, stop_dicom_actor
from core.config import get_settings
from core.logging import get_logger, setup_logging
from db.neo4j import neo4j_client
from service.dicom_service import dicom_service
from service.graph_service import graph_service
from service.pynetdicom_service import pynetdicom_service

logger = get_logger("gosmart_gs.cli")

cli_app = typer.Typer(
    name="gosmart-gs",
    help="CLI management tool for GoSmart DICOM Graph Server",
    add_completion=False,
)
console = Console()


@cli_app.command()
def start(
    host: Optional[str] = typer.Option(None, "--host", "-h", help="Bind host (overrides GOSMART_GS_HOST)"),
    port: Optional[int] = typer.Option(None, "--port", "-p", help="HTTP port (overrides GOSMART_GS_PORT)"),
    reload: bool = typer.Option(True, "--reload/--no-reload", help="Enable auto-reload"),
):
    """Start the FastAPI backend server (HTTP API & MCP SSE endpoints)."""
    settings = get_settings()
    bind_host = host or settings.HOST
    bind_port = port or settings.PORT

    console.print(Panel(
        f"[bold green]Starting GoSmart DICOM Graph Server[/bold green]\n"
        f"• HTTP & OpenAPI: [cyan]http://{bind_host}:{bind_port}/docs[/cyan]\n"
        f"• MCP SSE Endpoint: [cyan]http://{bind_host}:{bind_port}/mcp/sse[/cyan]\n"
        f"• Storage Root: [yellow]{settings.STORAGE_ROOT_URI}[/yellow]\n"
        f"• Neo4j Connection: [magenta]{settings.DB_CONNECTION_URI}[/magenta]\n"
        f"• Log Auto-Rotation: [blue]{settings.LOG_ROTATION_DAYS} days (Retain {settings.LOG_BACKUP_COUNT})[/blue]",
        title="GoSmart DICOM Graph Server",
    ))

    uvicorn.run("main:app", host=bind_host, port=bind_port, reload=reload)


@cli_app.command()
def health():
    """Check connectivity and health of Neo4j, Pykka Actor, and Storage."""
    setup_logging()
    settings = get_settings()

    async def _check():
        neo4j_ok = await neo4j_client.verify_connectivity()
        await neo4j_client.close()
        return neo4j_ok

    neo4j_status = asyncio.run(_check())

    actor = get_dicom_actor()
    actor_ok = actor.is_alive()
    actor_info = {}
    if actor_ok:
        try:
            actor_info = actor.ask({"type": "PING"}, timeout=2.0)
        except Exception as e:
            actor_info = {"error": str(e)}
    stop_dicom_actor()

    table = Table(title="System Health & Status")
    table.add_column("Component", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Details", style="dim")

    table.add_row(
        "Neo4j Database",
        "[green]HEALTHY[/green]" if neo4j_status else "[red]UNREACHABLE[/red]",
        settings.DB_CONNECTION_URI,
    )
    table.add_row(
        "Pykka Actor",
        "[green]HEALTHY[/green]" if actor_ok else "[red]DOWN[/red]",
        f"Processed count: {actor_info.get('processed_count', 0)}",
    )
    table.add_row(
        "Storage Root",
        "[green]CONFIGURED[/green]",
        settings.STORAGE_ROOT_URI,
    )
    table.add_row(
        "Log Rotation",
        f"[green]{settings.LOG_ROTATION_DAYS} Days[/green]",
        f"{settings.LOG_DIR}/{settings.LOG_FILE} (backups: {settings.LOG_BACKUP_COUNT})",
    )

    console.print(table)


@cli_app.command()
def mock(
    patient_id: str = typer.Option("PT-CLI-001", "--patient-id", "-p", help="Patient ID"),
    patient_name: str = typer.Option("DOE^JANE", "--patient-name", "-n", help="Patient Name"),
    modality: str = typer.Option("CT", "--modality", "-m", help="Modality (e.g. CT, MR)"),
    desc: str = typer.Option("CLI Mock Study", "--desc", "-d", help="Study Description"),
):
    """Generate a realistic mock DICOM instance and ingest it into Neo4j."""
    setup_logging()
    console.print(f"[cyan]Generating mock DICOM for patient {patient_id} ({modality})...[/cyan]")

    async def _run():
        ds = dicom_service.create_mock_dicom(
            patient_id=patient_id,
            patient_name=patient_name,
            modality=modality,
            study_description=desc,
        )
        hierarchy = dicom_service.parse_dataset(ds)
        saved_uri = dicom_service.save_raw_dicom(ds, hierarchy.instance.sop_instance_uid)
        hierarchy.instance.storage_uri = saved_uri

        result = None
        try:
            result = await graph_service.save_hierarchy(hierarchy)
        except Exception as e:
            console.print(f"[yellow]⚠ Notice: Neo4j offline or unreachable ({e}). File saved to disk.[/yellow]")
        await neo4j_client.close()
        return hierarchy, saved_uri, result

    hierarchy, saved_uri, result = asyncio.run(_run())
    console.print(f"[green]✓ DICOM Saved to:[/green] {saved_uri}")
    console.print(f"[green]✓ Study Instance UID:[/green] {hierarchy.study.study_instance_uid}")
    console.print(f"[green]✓ SOP Instance UID:[/green] {hierarchy.instance.sop_instance_uid}")
    if result:
        console.print(f"[green]✓ Neo4j Graph Record:[/green] {result}")


@cli_app.command()
def ingest(
    file_path: str = typer.Argument(..., help="Path to local DICOM (.dcm) file"),
    use_actor: bool = typer.Option(False, "--actor", "-a", help="Process asynchronously via Pykka actor"),
):
    """Ingest a DICOM file into local storage and the Neo4j knowledge graph."""
    setup_logging()
    path = Path(file_path).resolve()
    if not path.exists():
        console.print(f"[red]Error: File not found at {path}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[cyan]Parsing DICOM file: {path}...[/cyan]")

    if use_actor:
        actor = get_dicom_actor()
        res = actor.ask({"type": "INGEST_FILE", "file_path": str(path)})
        stop_dicom_actor()
        console.print(f"[green]Actor Ingestion Result:[/green] {res}")
        return

    async def _run():
        hierarchy = dicom_service.parse_file(str(path))
        saved_uri = dicom_service.save_raw_dicom(path.read_bytes(), hierarchy.instance.sop_instance_uid)
        hierarchy.instance.storage_uri = saved_uri
        res = None
        try:
            res = await graph_service.save_hierarchy(hierarchy)
        except Exception as e:
            console.print(f"[yellow]⚠ Notice: Neo4j offline or unreachable ({e}). File saved to disk.[/yellow]")
        await neo4j_client.close()
        return hierarchy, saved_uri, res

    hierarchy, saved_uri, res = asyncio.run(_run())
    console.print(f"[green]✓ Ingested Patient ID:[/green] {hierarchy.patient.patient_id}")
    console.print(f"[green]✓ Modality:[/green] {hierarchy.series.modality}")
    console.print(f"[green]✓ SOP Instance UID:[/green] {hierarchy.instance.sop_instance_uid}")
    console.print(f"[green]✓ Storage URI:[/green] {saved_uri}")


@cli_app.command()
def patients(
    limit: int = typer.Option(50, "--limit", "-l", help="Max patients to return"),
):
    """List patients indexed in the Neo4j knowledge graph."""
    setup_logging()
    async def _run():
        res = await graph_service.list_patients(limit=limit)
        await neo4j_client.close()
        return res

    patients_list = asyncio.run(_run())
    if not patients_list:
        console.print("[yellow]No patients found (or Neo4j offline).[/yellow]")
        return

    table = Table(title=f"Patients in Graph (Limit {limit})")
    table.add_column("Patient ID", style="cyan")
    table.add_column("Name", style="magenta")
    table.add_column("Birth Date")
    table.add_column("Sex")
    table.add_column("Studies", justify="right")

    for p in patients_list:
        table.add_row(
            str(p.get("patient_id", "")),
            str(p.get("patient_name", "") or ""),
            str(p.get("patient_birth_date", "") or ""),
            str(p.get("patient_sex", "") or ""),
            str(p.get("study_count", 0)),
        )
    console.print(table)


@cli_app.command()
def query(
    cypher: str = typer.Argument(..., help="Cypher query to execute"),
):
    """Run an arbitrary Cypher query against Neo4j and display results."""
    setup_logging()
    async def _run():
        res = await graph_service.execute_custom_query(cypher)
        await neo4j_client.close()
        return res

    records = asyncio.run(_run())
    console.print(Panel(json.dumps(records, indent=2, default=str), title=f"Cypher Results ({len(records)} records)"))


@cli_app.command()
def echo(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Target SCP host"),
    port: int = typer.Option(11112, "--port", "-p", help="Target SCP port"),
    called_aet: Optional[str] = typer.Option(None, "--called-aet", help="Target SCP AE Title"),
):
    """Perform DICOM C-ECHO verification ping against a remote PACS / SCP."""
    console.print(f"[cyan]Sending C-ECHO to {host}:{port}...[/cyan]")
    res = pynetdicom_service.echo_scu(host=host, port=port, called_aet=called_aet)
    if res.get("success"):
        console.print(f"[green]✓ C-ECHO Success:[/green] {res.get('message')}")
    else:
        console.print(f"[red]✗ C-ECHO Failed:[/red] {res.get('message')} (Status: {res.get('status_code')})")


@cli_app.command()
def mcp(
    transport: str = typer.Option("sse", "--transport", "-t", help="Transport mode: sse or stdio"),
    port: int = typer.Option(8001, "--port", "-p", help="Port for standalone SSE server"),
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host for standalone SSE server"),
):
    """Run the Model Context Protocol (MCP) server directly via SSE or STDIO."""
    from service.mcp_service import mcp as mcp_instance

    if transport.lower() == "stdio":
        console.print("[cyan]Running MCP Server over STDIO...[/cyan]")
        asyncio.run(mcp_instance.run_stdio_async())
    else:
        console.print(f"[cyan]Running standalone MCP SSE Server on http://{host}:{port}/sse ...[/cyan]")
        sse_starlette_app = mcp_instance.sse_app()
        uvicorn.run(sse_starlette_app, host=host, port=port)


if __name__ == "__main__":
    cli_app()
