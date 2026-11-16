import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
script_path = PROJECT_ROOT / "devop-scripts" / "cleanup-data.py"

spec = importlib.util.spec_from_file_location("cleanup_data", script_path)
cleanup_data_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup_data_mod)

CleanupData = cleanup_data_mod.CleanupData
DEFAULT_DATA_NODES = cleanup_data_mod.DEFAULT_DATA_NODES
PRESERVED_CONFIGURATION_LABELS = cleanup_data_mod.PRESERVED_CONFIGURATION_LABELS


@pytest.fixture
def mock_cleanup():
    """Create a CleanupData instance with mocked Neo4j driver and session."""
    with (
        patch.object(cleanup_data_mod, "Neo4jClient") as mock_client_cls,
        patch.object(cleanup_data_mod, "MediaService") as mock_ms_cls,
    ):
        mock_client = MagicMock()
        mock_driver = MagicMock()
        mock_session = MagicMock()

        mock_client.get_sync_driver.return_value = mock_driver
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_client_cls.return_value = mock_client

        mock_ms = MagicMock()
        mock_ms_cls.return_value = mock_ms

        cleanup = CleanupData()
        cleanup.client = mock_client
        cleanup.driver = mock_driver
        cleanup.media_service = mock_ms

        yield cleanup, mock_session, mock_ms


def test_preserved_configuration_labels_not_deleted(mock_cleanup):
    cleanup, mock_session, _ = mock_cleanup

    # Simulate query returning count of 10 for User
    mock_session.run.return_value = [MagicMock(data=lambda: {"count": 10})]

    # Attempt to clean preserved labels
    result = cleanup.cleanup_nodes(nodes=["User", "Group", "Identity", "Instance"])

    # User, Group, Identity must be skipped and never deleted
    assert "User" not in result
    assert "Group" not in result
    assert "Identity" not in result
    assert "Instance" in result


def test_cleanup_nodes_dry_run_vs_live(mock_cleanup):
    cleanup, mock_session, _ = mock_cleanup

    # Mock count query
    mock_session.run.return_value = [MagicMock(data=lambda: {"count": 5})]

    # Dry-run
    dry_result = cleanup.cleanup_nodes(nodes=["Instance"], dry_run=True)
    assert dry_result["Instance"] == 5

    # Verify DETACH DELETE was NOT called during dry-run
    queries_run = [call[0][0] for call in mock_session.run.call_args_list]
    assert not any("DETACH DELETE" in q for q in queries_run)

    mock_session.run.reset_mock()
    mock_session.run.return_value = [MagicMock(data=lambda: {"count": 5})]

    # Live run
    live_result = cleanup.cleanup_nodes(nodes=["Instance"], dry_run=False)
    assert live_result["Instance"] == 5
    live_queries = [call[0][0] for call in mock_session.run.call_args_list]
    assert any("DETACH DELETE" in q for q in live_queries)


def test_cleanup_media(mock_cleanup):
    cleanup, mock_session, mock_ms = mock_cleanup

    # Return 2 instances with storage_uri
    mock_session.run.return_value = [
        MagicMock(data=lambda: {"id": "inst-1", "storage_uri": "file:///tmp/dicom/1.dcm"}),
        MagicMock(data=lambda: {"id": "inst-2", "storage_uri": "file:///tmp/dicom/2.dcm"}),
    ]

    mock_fs = MagicMock()
    # 1.dcm exists, 2.dcm does not exist
    mock_fs.exists.side_effect = lambda path: "1.dcm" in path
    mock_ms.parse_uri.side_effect = lambda uri: MagicMock(fsspec_path=uri.replace("file://", ""))
    mock_ms.get_filesystem.return_value = mock_fs

    summary = cleanup.cleanup_media(dry_run=False)

    assert summary["total_instances"] == 2
    assert summary["unique_uris"] == 2
    assert summary["deleted_count"] == 1
    assert summary["not_found_count"] == 1
    assert summary["skipped_count"] == 0
    assert summary["failed_count"] == 0
    mock_fs.rm.assert_called_once_with("/tmp/dicom/1.dcm")


def test_cleanup_media_skips_protected_tests_assets(mock_cleanup):
    cleanup, mock_session, mock_ms = mock_cleanup

    asset_uri_1 = f"file://{PROJECT_ROOT / 'tests' / 'assets' / 'mr' / 'sample1.dcm'}"
    asset_uri_2 = f"file://{PROJECT_ROOT / 'tests' / 'assets' / 'mr' / '..' / 'mr' / 'sample2.dcm'}"
    deletable_uri = "file:///tmp/dicom/deletable.dcm"

    mock_session.run.return_value = [
        MagicMock(data=lambda: {"id": "inst-asset-1", "storage_uri": asset_uri_1}),
        MagicMock(data=lambda: {"id": "inst-asset-2", "storage_uri": asset_uri_2}),
        MagicMock(data=lambda: {"id": "inst-deletable", "storage_uri": deletable_uri}),
    ]

    mock_fs = MagicMock()
    mock_fs.exists.return_value = True
    mock_ms.parse_uri.side_effect = lambda uri: MagicMock(
        fsspec_protocol="file",
        fsspec_path=uri.replace("file://", ""),
    )
    mock_ms.get_filesystem.return_value = mock_fs

    summary = cleanup.cleanup_media(dry_run=False)

    assert summary["total_instances"] == 3
    assert summary["unique_uris"] == 3
    assert summary["deleted_count"] == 1
    assert summary["not_found_count"] == 0
    assert summary["skipped_count"] == 2
    assert summary["failed_count"] == 0
    mock_fs.exists.assert_called_once_with("/tmp/dicom/deletable.dcm")
    mock_fs.rm.assert_called_once_with("/tmp/dicom/deletable.dcm")


def test_cleanup_media_does_not_skip_cloud_uri_with_tests_assets_path(mock_cleanup):
    cleanup, mock_session, mock_ms = mock_cleanup

    cloud_uri = "gs://tests/assets/mr/sample1.dcm"
    mock_session.run.return_value = [
        MagicMock(data=lambda: {"id": "inst-gcs", "storage_uri": cloud_uri}),
    ]

    mock_fs = MagicMock()
    mock_fs.exists.return_value = True
    mock_ms.parse_uri.side_effect = lambda uri: MagicMock(
        fsspec_protocol="gs",
        fsspec_path=uri.replace("gs://", ""),
    )
    mock_ms.get_filesystem.return_value = mock_fs

    summary = cleanup.cleanup_media(dry_run=False)

    assert summary["deleted_count"] == 1
    assert summary["skipped_count"] == 0
    mock_fs.rm.assert_called_once_with("tests/assets/mr/sample1.dcm")


def test_cleanup_parent_recursion_stops_at_highest_node(mock_cleanup):
    cleanup, mock_session, _ = mock_cleanup

    # When query runs for Patient (highest_node), it has label Patient and no higher parents
    mock_session.run.return_value = [
        MagicMock(
            data=lambda: {
                "current_id": "p-1",
                "current_labels": ["Patient"],
                "parent_id": None,
                "parent_labels": None,
            }
        )
    ]

    deleted = cleanup.cleanup_parent(node_ids=["p-1"], highest_node="Patient", dry_run=False)
    assert deleted == 1

    # Ensure DETACH DELETE was called for Patient
    delete_queries = [call[0][0] for call in mock_session.run.call_args_list if "DETACH DELETE" in call[0][0]]
    assert len(delete_queries) == 1


def test_cleanup_parent_cascades_orphans(mock_cleanup):
    cleanup, mock_session, _ = mock_cleanup

    records_inst = [
        MagicMock(
            data=lambda: {
                "current_id": "i-1",
                "current_labels": ["Instance"],
                "parent_id": "se-1",
                "parent_labels": ["Series"],
            }
        )
    ]
    records_orphan_check_se = [MagicMock(data=lambda: {"orphaned_id": "se-1"})]
    records_series = [
        MagicMock(
            data=lambda: {
                "current_id": "se-1",
                "current_labels": ["Series"],
                "parent_id": "st-1",
                "parent_labels": ["Study"],
            }
        )
    ]
    records_orphan_check_st = []  # Study still has other children!

    mock_session.run.side_effect = [
        records_inst,  # 1. query current & parents for i-1
        [],  # 2. DETACH DELETE i-1
        records_orphan_check_se,  # 3. orphan check for se-1 -> orphaned!
        records_series,  # 4. query current & parents for se-1
        [],  # 5. DETACH DELETE se-1
        records_orphan_check_st,  # 6. orphan check for st-1 -> not orphaned!
    ]

    total_deleted = cleanup.cleanup_parent(node_ids=["i-1"], highest_node="Patient", dry_run=False)
    assert total_deleted == 2  # i-1 and se-1 deleted, st-1 preserved


def test_cleanup_all(mock_cleanup):
    cleanup, _, _ = mock_cleanup

    with patch.object(cleanup, "cleanup_media") as mock_media, patch.object(cleanup, "cleanup_nodes") as mock_nodes:
        mock_media.return_value = {"deleted_count": 3}
        mock_nodes.return_value = {"Instance": 3, "Patient": 1}

        summary = cleanup.cleanup_all(dry_run=False)
        assert summary["media"]["deleted_count"] == 3
        assert summary["nodes"]["Instance"] == 3
        assert "test_nodes" in summary
        mock_media.assert_called_once_with(dry_run=False)
        mock_nodes.assert_called_once_with(dry_run=False)


def test_cleanup_test_nodes_dry_run_vs_live(mock_cleanup):
    cleanup, mock_session, _ = mock_cleanup

    # Mock count query returning 2 nodes for each of the 7 target labels
    mock_session.run.return_value = [MagicMock(data=lambda: {"count": 2})]

    # Dry-run
    dry_result = cleanup.cleanup_test_nodes(dry_run=True)
    assert dry_result["dry_run"] is True
    assert dry_result["deleted_count"] == 14
    assert dry_result["failed_count"] == 0
    assert dry_result["errors"] == []
    assert dry_result["deleted_by_label"]["User"] == 2

    # Verify DETACH DELETE was NOT called during dry-run and n.test_id filter is used
    dry_queries = [call[0][0] for call in mock_session.run.call_args_list]
    assert len(dry_queries) == 7
    assert not any("DETACH DELETE" in q for q in dry_queries)
    assert all("n.test_id IS NOT NULL AND n.test_id <> ''" in q for q in dry_queries)

    mock_session.run.reset_mock()
    mock_session.run.return_value = [MagicMock(data=lambda: {"count": 2})]

    # Live run
    live_result = cleanup.cleanup_test_nodes(dry_run=False)
    assert live_result["dry_run"] is False
    assert live_result["deleted_count"] == 14
    assert live_result["failed_count"] == 0
    assert live_result["errors"] == []
    live_queries = [call[0][0] for call in mock_session.run.call_args_list]
    delete_queries = [q for q in live_queries if "DETACH DELETE" in q]
    assert len(delete_queries) == 7
    assert all("n.test_id IS NOT NULL AND n.test_id <> ''" in q for q in delete_queries)


def test_cleanup_test_nodes_zero_count_skips_delete(mock_cleanup):
    cleanup, mock_session, _ = mock_cleanup

    mock_session.run.return_value = [MagicMock(data=lambda: {"count": 0})]

    result = cleanup.cleanup_test_nodes(dry_run=False)
    assert result["deleted_count"] == 0
    assert result["failed_count"] == 0
    assert all(cnt == 0 for cnt in result["deleted_by_label"].values())
    queries = [call[0][0] for call in mock_session.run.call_args_list]
    assert not any("DETACH DELETE" in q for q in queries)
