import json
import logging
import tempfile
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from core.config import Settings
from core.logging import (
    Neo4jAlreadyExistsLogFilter,
    bind_log_context,
    clear_log_context,
    get_logger,
    setup_logging,
)


def test_logging_setup_and_7_day_rotation():
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            LOG_DIR=tmpdir,
            LOG_FILE="test_rotation.log",
            LOG_ROTATION_DAYS=7,
            LOG_BACKUP_COUNT=8,
            LOG_TO_FILE=True,
            LOG_FORMAT="json",
        )
        setup_logging(settings)

        # Verify handlers on root logger
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, TimedRotatingFileHandler)]
        assert len(file_handlers) >= 1
        fh = file_handlers[0]

        # Verify 7-day rotation configuration
        assert fh.when == "D"
        assert fh.interval == 7 * 86400 or fh.interval == 7  # 7 days
        assert fh.backupCount == 8


def test_structlog_json_values_and_context():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test_json.log"
        settings = Settings(
            LOG_DIR=tmpdir,
            LOG_FILE="test_json.log",
            LOG_TO_FILE=True,
            LOG_FORMAT="json",
        )
        setup_logging(settings)

        logger = get_logger("test.structlog")

        # Bind context variables
        bind_log_context(request_id="REQ-12345", session_user="radiologist_1")

        # Emit log with complex JSON log values
        metadata_payload = {
            "patient_id": "PT-999",
            "modality": "CT",
            "slice_thickness_mm": 1.25,
            "tags": ["head", "contrast", "axial"],
            "series_count": 3,
        }
        logger.info(
            "dicom_ingestion_event",
            status="SUCCESS",
            dicom_metadata=metadata_payload,
            execution_time_ms=45.6,
        )

        clear_log_context()

        # Flush handlers
        for h in logging.getLogger().handlers:
            h.flush()

        # Verify content written to rotating file
        assert log_path.exists()
        lines = [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) >= 1

        last_log = json.loads(lines[-1])

        # Verify structured JSON attributes
        assert last_log["event"] == "dicom_ingestion_event"
        assert last_log["request_id"] == "REQ-12345"
        assert last_log["session_user"] == "radiologist_1"
        assert last_log["status"] == "SUCCESS"
        assert last_log["execution_time_ms"] == 45.6
        assert last_log["dicom_metadata"]["patient_id"] == "PT-999"
        assert last_log["dicom_metadata"]["slice_thickness_mm"] == 1.25
        assert last_log["dicom_metadata"]["tags"] == ["head", "contrast", "axial"]
        assert "timestamp" in last_log
        assert "level" in last_log


def test_neo4j_already_exists_notification_filter():
    """Verify that verbose Neo4j schema 'already exists' notifications are suppressed."""
    filter_instance = Neo4jAlreadyExistsLogFilter()

    # Record with 'already exists' note
    record_already_exists = logging.LogRecord(
        name="neo4j.notifications",
        level=logging.INFO,
        pathname="result.py",
        lineno=346,
        msg=(
            "Received notification from DBMS server: <GqlStatusObject gql_status='00NA0', "
            'status_description="note: successful completion - index or constraint already exists.">'
        ),
        args=(),
        exc_info=None,
    )
    assert filter_instance.filter(record_already_exists) is False

    # Record without 'already exists' note
    record_normal_warning = logging.LogRecord(
        name="neo4j.notifications",
        level=logging.WARNING,
        pathname="result.py",
        lineno=346,
        msg=(
            "Received notification from DBMS server: <GqlStatusObject gql_status='01000', "
            'status_description="warning: query plan sub-optimal">'
        ),
        args=(),
        exc_info=None,
    )
    assert filter_instance.filter(record_normal_warning) is True
