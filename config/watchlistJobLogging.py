"""Readable and structured file logs for one watchlist job invocation."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo


_list_name = ContextVar("watchlist_log_list", default="-")
_attempt = ContextVar("watchlist_log_attempt", default=0)


def set_list_context(name: str, attempt: int = 0):
    """Context for messages from the job and the existing pipeline modules."""
    return _list_name.set(name), _attempt.set(attempt)


def reset_list_context(tokens) -> None:
    _list_name.reset(tokens[0])
    _attempt.reset(tokens[1])


class _Context(logging.Filter):
    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        record.watchlist_name = _list_name.get()
        record.attempt = _attempt.get()
        return True


class _ReadableFormatter(logging.Formatter):
    def __init__(self, timezone_name: str):
        super().__init__()
        self.zone = ZoneInfo(timezone_name)

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, self.zone).isoformat(
            timespec="seconds"
        )
        message = (
            f"{stamp} | {record.levelname:<7} | "
            f"run={record.run_id} list={record.watchlist_name} "
            f"attempt={record.attempt} | {record.getMessage()}"
        )
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        return message


class _JsonFormatter(logging.Formatter):
    _FIELDS = (
        "event",
        "status",
        "schedule",
        "reason",
        "elapsed_seconds",
        "watchlist_file_id",
        "duplicate_status",
        "core_new_count",
        "core_updated_count",
        "core_deleted_count",
        "core_skipped_count",
        "parsed_record_count",
        "processed_record_count",
        "raw_record_count",
        "last_successful_check",
        "start_time",
        "end_time",
        "error_type",
    )

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "job_run_id": record.run_id,
            "watchlist_name": record.watchlist_name,
            "attempt": record.attempt,
            "message": record.getMessage(),
        }

        for field in self._FIELDS:
            if hasattr(record, field):
                entry[field] = getattr(record, field)

        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)

        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_watchlist_job_logging(
    run_id: str,
    timezone_name: str,
    log_dir: Path,
) -> tuple[Path, Path, list[logging.Handler]]:
    """Attach handlers to the root so existing pipeline logs carry list context."""
    log_dir.mkdir(parents=True, exist_ok=True)

    text_path = log_dir / f"watchlist-{run_id}.log"
    json_path = log_dir / f"watchlist-{run_id}.jsonl"

    readable = _ReadableFormatter(timezone_name)
    context = _Context(run_id)

    text_file = RotatingFileHandler(
        text_path,
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    text_file.setFormatter(readable)

    json_file = RotatingFileHandler(
        json_path,
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    json_file.setFormatter(_JsonFormatter())

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(readable)

    handlers = [text_file, json_file, console]
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for handler in handlers:
        handler.addFilter(context)
        root.addHandler(handler)

    for name in (
        "elastic_transport.transport",
        "elasticsearch",
        "urllib3.connectionpool",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    return text_path, json_path, handlers


def close_watchlist_job_logging(handlers: list[logging.Handler]) -> None:
    root = logging.getLogger()
    for handler in handlers:
        root.removeHandler(handler)
        handler.close()