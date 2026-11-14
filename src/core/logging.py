import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars, unbind_contextvars
from structlog.stdlib import BoundLogger, LoggerFactory, ProcessorFormatter

from core.config import Settings, get_settings


class Neo4jAlreadyExistsLogFilter(logging.Filter):
    """Filter out noisy Neo4j notifications when schema constraints or indexes already exist."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "already exists" in message or "00NA0" in message:
            return False
        return True


def setup_logging(settings: Optional[Settings] = None) -> None:
    """Configure industrial-strength structured logging with structlog and 7-day file auto-rotation."""
    cfg = settings or get_settings()
    log_level = getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO)

    # 1. Pre-chain for standard library log records arriving at the handler
    foreign_pre_chain = [
        merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # 2. Configure structlog pipeline
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=LoggerFactory(),
        wrapper_class=BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 3. Create Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    if cfg.LOG_FORMAT.lower() == "console":
        console_formatter = ProcessorFormatter(
            foreign_pre_chain=foreign_pre_chain,
            processors=[
                ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
        )
    else:
        # Default to JSON for structured output
        console_formatter = ProcessorFormatter(
            foreign_pre_chain=foreign_pre_chain,
            processors=[
                ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    console_handler.setFormatter(console_formatter)

    handlers = [console_handler]

    # 4. Create 7-Day Timed Rotating File Handler (JSON lines)
    if cfg.LOG_TO_FILE:
        log_dir = Path(cfg.LOG_DIR).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file_path = log_dir / cfg.LOG_FILE

        file_handler = TimedRotatingFileHandler(
            filename=str(log_file_path),
            when="D",
            interval=cfg.LOG_ROTATION_DAYS,  # Auto rotate every 7 days by default
            backupCount=cfg.LOG_BACKUP_COUNT,  # Keep 8 rotation backups
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)

        file_formatter = ProcessorFormatter(
            foreign_pre_chain=foreign_pre_chain,
            processors=[
                ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )

        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)

    # 5. Attach handlers to the standard library root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)
    for handler in handlers:
        root_logger.addHandler(handler)

    # Re-route uvicorn & framework loggers through the same handlers
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "pynetdicom", "neo4j"):
        sub_logger = logging.getLogger(logger_name)
        sub_logger.handlers.clear()
        sub_logger.propagate = True

    # Quiet Neo4j DBMS informational notifications (such as "already exists" schema notices)
    neo4j_notif_logger = logging.getLogger("neo4j.notifications")
    neo4j_notif_logger.setLevel(logging.WARNING)
    neo4j_notif_logger.addFilter(Neo4jAlreadyExistsLogFilter())


def get_logger(name: Optional[str] = None) -> BoundLogger:
    """Return a configured structlog BoundLogger instance."""
    return structlog.get_logger(name)


def bind_log_context(**kwargs: Any) -> None:
    """Bind contextual key-value pairs (JSON serializable) for all subsequent logs in the current context."""
    bind_contextvars(**kwargs)


def unbind_log_context(*keys: str) -> None:
    """Unbind specific contextual keys."""
    unbind_contextvars(*keys)


def clear_log_context() -> None:
    """Clear all contextual log values."""
    clear_contextvars()
