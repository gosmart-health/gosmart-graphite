#!/usr/bin/env python3
"""
CLI DevOps utility to clean up database nodes and DICOM media files
to prepare for a fresh test while strictly preserving basic configurations
(e.g., User, Group, Identity, Authenticator, DICOMConfiguration, System, Schema Constraints).
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

# Ensure project root and src/ directory are available on sys.path regardless of execution directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
for _p in (SRC_DIR, PROJECT_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from neo4j.exceptions import DriverError  # noqa: E402

from core.config import Settings, get_settings  # noqa: E402
from core.logging import get_logger  # noqa: E402
from db.neo4j import Neo4jClient  # noqa: E402
from service.media_service import MediaService  # noqa: E402

logger = get_logger("gosmart_gs.devop.cleanup")

# Configuration labels that must never be deleted during test cleanup
PRESERVED_CONFIGURATION_LABELS: Set[str] = {
    "User",
    "Identity",
    "Authenticator",
    "Group",
    "DICOMConfiguration",
    "System",
    "ClientApp",
    "Role",
    "Scope",
}

# Default list of data nodes to clean up (as defined in specifications)
DEFAULT_DATA_NODES: List[str] = [
    "AuditLog",
    "Instance",
    "Series",
    "Study",
    "Patient",
    "FHIR_Patient",
    "Encounter",
    "ImagingStudy",
    "ServiceRequest",
    "ProcedureRequest",
    "DiagnosticReport",
    "DocumentReference",
    "Procedure",
    "Condition",
    "Location",
    "Observation",
    "TransferStatus",
]


class CleanupData:
    """DevOps utility class to clean up database nodes and DICOM media files."""

    def __init__(
        self,
        env_file: Optional[Union[str, Path]] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        """
        Connect to the Neo4j database as provided in ../.env file
        and assign the client to self.client.
        """
        script_dir = Path(__file__).resolve().parent
        candidate_env_paths = [
            Path(env_file) if env_file else None,
            (script_dir / "../.env").resolve(),
            PROJECT_ROOT / ".env",
            Path.cwd() / ".env",
        ]

        resolved_env_file: Optional[Path] = None
        for cand in candidate_env_paths:
            if cand and cand.is_file():
                resolved_env_file = cand
                break

        if settings is not None:
            self.settings = settings
        elif resolved_env_file:
            self.settings = Settings(_env_file=str(resolved_env_file))
        else:
            self.settings = get_settings()

        # Have the client assigned to self.client
        self.client = Neo4jClient(settings=self.settings)
        self.driver = self.client.get_sync_driver()
        self.media_service = MediaService(settings=self.settings)
        self.default_nodes = list(dict.fromkeys(DEFAULT_DATA_NODES))

    def run_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Run a synchronous Cypher query on the target database."""
        target_db = self.settings.DB_DATABASE
        with self.driver.session(database=target_db) as session:
            result = session.run(query, parameters or {})  # type: ignore[arg-type]
            return [record.data() for record in result]

    def cleanup_media(self, node: str = "Instance", dry_run: bool = False) -> Dict[str, Any]:
        """
        Get all the nodes of the specified label in the database.
        The storage_uri points to where the media is; delete it.

        Returns a dictionary summary with counts and status.
        """
        # Validate node label to prevent Cypher injection
        clean_node = "".join(c for c in node if c.isalnum() or c == "_")
        query = f"""
        MATCH (n:{clean_node})
        WHERE n.storage_uri IS NOT NULL AND n.storage_uri <> ''
        RETURN n.id AS id, n.storage_uri AS storage_uri
        """

        records = self.run_query(query)
        total_records = len(records)
        unique_uris = {r["storage_uri"] for r in records if r.get("storage_uri")}

        deleted_count = 0
        not_found_count = 0
        failed_count = 0
        errors: List[str] = []

        logger.info(
            "cleanup_media_started",
            node=clean_node,
            total_instances=total_records,
            unique_uris=len(unique_uris),
            dry_run=dry_run,
        )

        for uri in sorted(unique_uris):
            try:
                parsed = self.media_service.parse_uri(uri)
                fs = self.media_service.get_filesystem(parsed)
                exists = bool(fs.exists(parsed.fsspec_path))

                if not exists:
                    not_found_count += 1
                    logger.debug("media_file_not_found", uri=uri)
                    continue

                if dry_run:
                    deleted_count += 1
                    logger.debug("dry_run_media_file_would_delete", uri=uri)
                else:
                    fs.rm(parsed.fsspec_path)
                    deleted_count += 1
                    logger.debug("media_file_deleted", uri=uri)
            except Exception as e:
                failed_count += 1
                err_msg = f"Failed to delete {uri}: {e}"
                errors.append(err_msg)
                logger.warning("media_delete_failed", uri=uri, error=str(e))

        summary = {
            "node": clean_node,
            "total_instances": total_records,
            "unique_uris": len(unique_uris),
            "deleted_count": deleted_count,
            "not_found_count": not_found_count,
            "failed_count": failed_count,
            "errors": errors,
            "dry_run": dry_run,
        }

        logger.info(
            "cleanup_media_completed",
            deleted=deleted_count,
            not_found=not_found_count,
            failed=failed_count,
            dry_run=dry_run,
        )
        return summary

    def cleanup_parent(
        self,
        node_ids: list,
        highest_node: str = "Patient",
        dry_run: bool = False,
    ) -> int:
        """
        Gather parent nodes ids and remove current node.
        Once removed, recursively call cleanup_parent except when:
        1. The current node reaches highest_node (e.g. "Patient").
        2. The parent node still has other remaining children (not orphaned).
        3. No parent nodes exist.

        Returns total count of removed nodes across all recursion levels.
        """
        if not node_ids:
            return 0

        # Clean string IDs to prevent type issues
        clean_ids = [str(nid) for nid in node_ids if nid]
        if not clean_ids:
            return 0

        # Gather parent node IDs and labels for current nodes
        query = """
        MATCH (current)
        WHERE current.id IN $node_ids
        OPTIONAL MATCH (parent)-[r:HAS_STUDY|HAS_SERIES|HAS_INSTANCE]->(current)
        RETURN current.id AS current_id,
               labels(current) AS current_labels,
               parent.id AS parent_id,
               labels(parent) AS parent_labels
        """
        records = self.run_query(query, {"node_ids": clean_ids})
        if not records:
            return 0

        # Collect current IDs and determine if highest_node was reached
        current_id_set = {r["current_id"] for r in records if r.get("current_id")}
        is_highest = any(highest_node in (r.get("current_labels") or []) for r in records)

        # Collect distinct parent IDs
        parent_ids = list({r["parent_id"] for r in records if r.get("parent_id")})

        deleted_count = len(current_id_set)

        if not dry_run:
            delete_query = """
            MATCH (current)
            WHERE current.id IN $node_ids
            DETACH DELETE current
            """
            self.run_query(delete_query, {"node_ids": list(current_id_set)})
            logger.info("cleanup_parent_deleted_current", count=deleted_count, node_ids=list(current_id_set))
        else:
            logger.info("dry_run_cleanup_parent_would_delete", count=deleted_count, node_ids=list(current_id_set))

        # Stop recursion if highest_node was reached or no parents exist
        if is_highest or not parent_ids:
            return deleted_count

        # Gather only parents that have no remaining children (orphans)
        if not dry_run:
            orphan_query = """
            MATCH (parent)
            WHERE parent.id IN $parent_ids
            OPTIONAL MATCH (parent)-[r:HAS_STUDY|HAS_SERIES|HAS_INSTANCE]->(remaining_child)
            WITH parent, count(remaining_child) AS child_count
            WHERE child_count = 0
            RETURN parent.id AS orphaned_id
            """
            orphan_records = self.run_query(orphan_query, {"parent_ids": parent_ids})
            orphaned_parent_ids = [r["orphaned_id"] for r in orphan_records if r.get("orphaned_id")]
        else:
            orphaned_parent_ids = parent_ids

        # Once removed recursively call cleanup_parent on orphaned parents
        if orphaned_parent_ids:
            deleted_count += self.cleanup_parent(
                node_ids=orphaned_parent_ids,
                highest_node=highest_node,
                dry_run=dry_run,
            )

        return deleted_count

    def cleanup_nodes(
        self,
        nodes: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, int]:
        """
        Remove the nodes in the nodes list while preserving configuration nodes.

        Returns a dictionary mapping each node label to the count of deleted nodes.
        """
        target_nodes = nodes or self.default_nodes
        # Deduplicate while preserving order
        deduped_nodes = list(dict.fromkeys(target_nodes))

        deleted_by_label: Dict[str, int] = {}

        logger.info("cleanup_nodes_started", target_labels=deduped_nodes, dry_run=dry_run)

        for label in deduped_nodes:
            # Skip if accidentally configured to delete preserved configuration labels
            if label in PRESERVED_CONFIGURATION_LABELS:
                logger.warning("skipping_preserved_configuration_label", label=label)
                continue

            clean_label = "".join(c for c in label if c.isalnum() or c == "_")

            # First get count of existing nodes
            count_query = f"MATCH (n:{clean_label}) RETURN count(n) AS count"
            count_records = self.run_query(count_query)
            existing_count = count_records[0]["count"] if count_records else 0

            if existing_count == 0:
                deleted_by_label[clean_label] = 0
                continue

            if dry_run:
                deleted_by_label[clean_label] = existing_count
                logger.info("dry_run_nodes_would_delete", label=clean_label, count=existing_count)
            else:
                delete_query = f"""
                MATCH (n:{clean_label})
                DETACH DELETE n
                """
                self.run_query(delete_query)
                deleted_by_label[clean_label] = existing_count
                logger.info("nodes_deleted", label=clean_label, count=existing_count)

        logger.info("cleanup_nodes_completed", deleted_summary=deleted_by_label, dry_run=dry_run)
        return deleted_by_label

    def cleanup_storage_directory(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Scan and clean up raw media files directly from STORAGE_ROOT_URI.
        Useful for clearing orphaned DICOM files on disk.
        """
        root_uri = self.settings.STORAGE_ROOT_URI
        deleted_count = 0
        failed_count = 0
        errors: List[str] = []

        try:
            parsed = self.media_service.parse_uri(root_uri)
            fs = self.media_service.get_filesystem(parsed)

            if not fs.exists(parsed.fsspec_path):
                return {"root_uri": root_uri, "deleted_count": 0, "status": "directory_not_found"}

            all_files = fs.find(parsed.fsspec_path)
            for file_path in all_files:
                try:
                    if dry_run:
                        deleted_count += 1
                    else:
                        fs.rm(file_path)
                        deleted_count += 1
                except Exception as ex:
                    failed_count += 1
                    errors.append(f"{file_path}: {ex}")

            return {
                "root_uri": root_uri,
                "total_files": len(all_files),
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "errors": errors,
                "dry_run": dry_run,
            }
        except Exception as e:
            logger.warning("cleanup_storage_directory_failed", root_uri=root_uri, error=str(e))
            return {
                "root_uri": root_uri,
                "deleted_count": 0,
                "failed_count": failed_count,
                "errors": [str(e)],
                "dry_run": dry_run,
            }

    def get_database_status(self) -> Dict[str, Any]:
        """Inspect the database and return counts of data vs configuration nodes."""
        query = "MATCH (n) RETURN labels(n) AS labels, count(n) AS count"
        records = self.run_query(query)

        data_nodes: Dict[str, int] = {}
        config_nodes: Dict[str, int] = {}
        other_nodes: Dict[str, int] = {}

        for r in records:
            lbls = r.get("labels") or []
            cnt = r.get("count", 0)
            lbl_name = ":".join(lbls) if lbls else "Unlabeled"

            is_config = any(label in PRESERVED_CONFIGURATION_LABELS for label in lbls)
            is_data = any(label in DEFAULT_DATA_NODES for label in lbls)

            if is_config:
                config_nodes[lbl_name] = cnt
            elif is_data:
                data_nodes[lbl_name] = cnt
            else:
                other_nodes[lbl_name] = cnt

        # Check media files
        media_records = self.run_query(
            "MATCH (i:Instance) WHERE i.storage_uri IS NOT NULL AND i.storage_uri <> '' "
            "RETURN count(i) AS instance_count, count(DISTINCT i.storage_uri) AS uri_count"
        )
        instance_media = media_records[0] if media_records else {"instance_count": 0, "uri_count": 0}

        return {
            "data_nodes": data_nodes,
            "config_nodes": config_nodes,
            "other_nodes": other_nodes,
            "instance_media": instance_media,
        }

    def cleanup_all(
        self,
        dry_run: bool = False,
        clean_storage_dir: bool = False,
    ) -> Dict[str, Any]:
        """
        Perform a complete test data cleanup:
        1. cleanup_media: removes DICOM media files referenced by Instance nodes.
        2. cleanup_nodes: removes all test/data nodes while strictly preserving configurations.
        3. (Optional) clean_storage_dir: clears remaining files in STORAGE_ROOT_URI.
        """
        media_summary = self.cleanup_media(dry_run=dry_run)
        nodes_summary = self.cleanup_nodes(dry_run=dry_run)

        storage_dir_summary = None
        if clean_storage_dir:
            storage_dir_summary = self.cleanup_storage_directory(dry_run=dry_run)

        return {
            "media": media_summary,
            "nodes": nodes_summary,
            "storage_dir": storage_dir_summary,
            "dry_run": dry_run,
        }

    def close(self) -> None:
        """Close driver connections."""
        if hasattr(self, "driver") and self.driver:
            try:
                self.driver.close()
            except DriverError, Exception:
                pass

    def __enter__(self) -> "CleanupData":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


def print_status_table(status: Dict[str, Any]) -> None:
    """Print a formatted report of current database status."""
    data_nodes_dict: Dict[str, int] = dict(status.get("data_nodes", {}))
    config_nodes_dict: Dict[str, int] = dict(status.get("config_nodes", {}))
    other_nodes_dict: Dict[str, int] = dict(status.get("other_nodes", {}))

    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console()
        console.print(Panel("[bold cyan]GoSmart DICOM Graph Server - Database & Storage Status[/bold cyan]"))

        # Data Nodes Table
        table_data = Table(title="[bold yellow]Data Nodes (Targeted for Test Cleanup)[/bold yellow]")
        table_data.add_column("Node Label", style="cyan")
        table_data.add_column("Count", justify="right", style="green")

        total_data = 0
        for lbl in sorted(data_nodes_dict.keys()):
            cnt = data_nodes_dict[lbl]
            table_data.add_row(lbl, str(cnt))
            total_data += cnt
        table_data.add_section()
        table_data.add_row("[bold]Total Data Nodes[/bold]", f"[bold]{total_data}[/bold]")
        console.print(table_data)

        # Preserved Config Table
        table_cfg = Table(title="[bold green]Configuration Nodes (Strictly Preserved)[/bold green]")
        table_cfg.add_column("Node Label", style="magenta")
        table_cfg.add_column("Count", justify="right", style="green")

        total_cfg = 0
        for lbl in sorted(config_nodes_dict.keys()):
            cnt = config_nodes_dict[lbl]
            table_cfg.add_row(lbl, str(cnt))
            total_cfg += cnt
        table_cfg.add_section()
        table_cfg.add_row("[bold]Total Preserved Nodes[/bold]", f"[bold]{total_cfg}[/bold]")
        console.print(table_cfg)

        if other_nodes_dict:
            table_other = Table(title="[bold blue]Other Nodes[/bold blue]")
            table_other.add_column("Node Label", style="blue")
            table_other.add_column("Count", justify="right", style="green")
            for lbl in sorted(other_nodes_dict.keys()):
                cnt = other_nodes_dict[lbl]
                table_other.add_row(lbl, str(cnt))
            console.print(table_other)

        # Media summary
        im = status["instance_media"]
        console.print(
            f"\n[bold]DICOM Media Files:[/bold] [cyan]{im.get('instance_count', 0)}[/cyan] instances "
            f"pointing to [cyan]{im.get('uri_count', 0)}[/cyan] unique storage URIs.\n"
        )
    except ImportError:
        print("\n=== Data Nodes (Targeted for Cleanup) ===")
        for lbl in sorted(data_nodes_dict.keys()):
            print(f"  {lbl}: {data_nodes_dict[lbl]}")
        print("\n=== Configuration Nodes (Strictly Preserved) ===")
        for lbl in sorted(config_nodes_dict.keys()):
            print(f"  {lbl}: {config_nodes_dict[lbl]}")
        im = status["instance_media"]
        print(f"\nMedia: {im.get('instance_count', 0)} instances ({im.get('uri_count', 0)} unique URIs)\n")


def main() -> None:
    """CLI Entrypoint for the dev op cleanup utility."""
    parser = argparse.ArgumentParser(
        description="DevOps CLI utility to clean up database nodes and DICOM media files to start a new test.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Inspect current status without changes:
  python devop-scripts/cleanup-data.py --status

  # Dry-run preview of what would be cleaned:
  python devop-scripts/cleanup-data.py --dry-run

  # Full cleanup with confirmation prompt:
  python devop-scripts/cleanup-data.py

  # Non-interactive cleanup (e.g. for CI / automated tests):
  python devop-scripts/cleanup-data.py --yes

  # Only clean media files:
  python devop-scripts/cleanup-data.py --media-only --yes

  # Only clean database nodes:
  python devop-scripts/cleanup-data.py --nodes-only --yes

  # Recursively clean specific node IDs and orphaned parents up to Patient:
  python devop-scripts/cleanup-data.py --node-ids ID1 ID2 --yes
        """,
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Display current database and media storage status without deleting anything.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the cleanup operations and display what would be removed without deleting.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Bypass interactive confirmation prompt.",
    )
    parser.add_argument(
        "--media-only",
        action="store_true",
        help="Only delete DICOM media files, leaving database nodes intact.",
    )
    parser.add_argument(
        "--nodes-only",
        action="store_true",
        help="Only delete database nodes, leaving media files intact.",
    )
    parser.add_argument(
        "--clean-storage-dir",
        action="store_true",
        help="Also remove leftover files directly from STORAGE_ROOT_URI.",
    )
    parser.add_argument(
        "--node-ids",
        nargs="+",
        help="Specify one or more node IDs to clean up recursively up to Patient.",
    )
    parser.add_argument(
        "--labels",
        help="Comma-separated list of specific node labels to clean up (e.g. 'Instance,Series,Study,Patient').",
    )
    parser.add_argument(
        "--env-file",
        help="Path to custom .env file (default: ../.env or project .env).",
    )

    args = parser.parse_args()

    # Initialize CleanupData
    with CleanupData(env_file=args.env_file) as cleanup:
        status = cleanup.get_database_status()

        if args.status:
            print_status_table(status)
            return

        # Specific node IDs cleanup
        if args.node_ids:
            if not args.dry_run and not args.yes and sys.stdin.isatty():
                ans = input(f"Are you sure you want to clean up node IDs {args.node_ids}? [y/N]: ").strip().lower()
                if ans not in ("y", "yes"):
                    print("Aborted.")
                    return
            deleted = cleanup.cleanup_parent(node_ids=args.node_ids, dry_run=args.dry_run)
            mode_str = "[DRY-RUN] Would remove" if args.dry_run else "Successfully removed"
            print(f"✨ {mode_str} {deleted} nodes in hierarchy.")
            return

        # Print preview before asking or executing
        print_status_table(status)

        if not args.dry_run and not args.yes:
            if sys.stdin.isatty():
                ans = input("⚠️  Are you sure you want to clean up test data and media files? [y/N]: ").strip().lower()
                if ans not in ("y", "yes"):
                    print("Cleanup cancelled.")
                    return
            else:
                print("Error: Non-interactive session without --yes flag. Use -y or --yes to confirm.", file=sys.stderr)
                sys.exit(1)

        # Parse custom labels if supplied
        custom_labels = None
        if args.labels:
            custom_labels = [lbl.strip() for lbl in args.labels.split(",") if lbl.strip()]

        # Execute operations
        if args.media_only:
            print(f"\n🚀 {'[DRY-RUN] ' if args.dry_run else ''}Executing DICOM Media Cleanup...")
            summary = cleanup.cleanup_media(dry_run=args.dry_run)
            deleted_cnt = summary["deleted_count"]
            not_found_cnt = summary["not_found_count"]
            print(f"✅ Media cleanup complete: {deleted_cnt} deleted, {not_found_cnt} not found.")
        elif args.nodes_only:
            print(f"\n🚀 {'[DRY-RUN] ' if args.dry_run else ''}Executing Database Nodes Cleanup...")
            summary = cleanup.cleanup_nodes(nodes=custom_labels, dry_run=args.dry_run)
            print("✅ Nodes cleanup complete:")
            for lbl, cnt in sorted(summary.items(), key=lambda x: x[0]):
                print(f"   - {lbl}: {cnt}")
        else:
            print(f"\n🚀 {'[DRY-RUN] ' if args.dry_run else ''}Executing Full Test Data & Media Cleanup...")
            summary = cleanup.cleanup_all(
                dry_run=args.dry_run,
                clean_storage_dir=args.clean_storage_dir,
            )
            media_s = summary["media"]
            nodes_s = summary["nodes"]
            print(f"✅ Media files deleted: {media_s['deleted_count']} ({media_s['not_found_count']} missing)")
            print("✅ Nodes deleted:")
            for lbl, cnt in sorted(nodes_s.items(), key=lambda x: x[0]):
                print(f"   - {lbl}: {cnt}")
            if summary.get("storage_dir"):
                s_dir = summary["storage_dir"]
                print(f"✅ Storage directory files removed: {s_dir['deleted_count']}")

        print("\n🎉 Done! Configuration nodes, users, groups, and schemas preserved.")


if __name__ == "__main__":
    main()
