"""Console logging and reusable, correlated logs for scheduled jobs."""

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


_source = ContextVar("job_source", default="-")
_attempt = ContextVar("job_attempt", default=0)
_stage = ContextVar("job_stage", default="JOB")
_MAIN_EVENTS = {
    "JOB_STARTED", "ATTEMPT_STARTED", "ATTEMPT_FAILED", "RETRY_WAIT",
    "SOURCE_SETUP_FAILED", "SOURCE_FINISHED", "JOB_FINISHED",
    "JOB_ALREADY_RUNNING", "JOB_ABORTED",
}


def _quiet_dependencies() -> None:
    for name in (
        "elastic_transport.transport", "elasticsearch", "urllib3.connectionpool"
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging() -> None:
    """Keep the existing console logging behavior for standalone pipelines."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    _quiet_dependencies()


class _JobContext(logging.Filter):
    def __init__(self, job_name: str, run_id: str):
        super().__init__()
        self.job_name = job_name
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.job_name = self.job_name
        record.job_run_id = self.run_id
        record.source_name = _source.get()
        record.attempt = _attempt.get()
        if not hasattr(record, "stage"):
            record.stage = _stage.get()
        return True


class _MainOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event", None) in _MAIN_EVENTS


class _IssueOnly(logging.Filter):
    def __init__(self, issues: dict[str, dict[str, int]]):
        super().__init__()
        self.issues = issues

    def filter(self, record: logging.LogRecord) -> bool:
        if (record.levelno < logging.WARNING or
                getattr(record, "event", None) in {"SOURCE_FINISHED", "JOB_FINISHED"}):
            return False
        source = _source.get()
        if source != "-":
            counts = self.issues.setdefault(source, {"warnings": 0, "errors": 0})
            counts["warnings" if record.levelno == logging.WARNING else "errors"] += 1
        return True


class _Readable(logging.Formatter):
    def __init__(self, zone: ZoneInfo | None, main: bool = False):
        super().__init__()
        self.zone = zone
        self.main = main

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created, timezone.utc).astimezone(self.zone).isoformat(
            timespec="seconds"
        )
        event = getattr(record, "event", "PIPELINE_LOG")
        data = getattr(record, "job_data", {})
        detail = record.getMessage()
        if detail == event:
            detail = ""
        elif detail.startswith(event + " "):
            detail = detail[len(event) + 1:]
        if self.main and event == "SOURCE_FINISHED":
            status = data["status"]
            if status == "FAILED":
                counts = "core_written=unknown counts=unavailable"
            elif status == "NOT_DUE":
                counts = "core_written=no core_changes=0"
            else:
                changed = any(data.get(key, 0) for key in (
                    "core_new_count", "core_updated_count", "core_deleted_count"
                ))
                counts = (
                    f"core_written={'yes' if changed else 'no'} "
                    f"insert={data.get('core_new_count', 0)} "
                    f"update={data.get('core_updated_count', 0)} "
                    f"delete={data.get('core_deleted_count', 0)} "
                    f"skipped={data.get('core_skipped_count', 0)}"
                )
            detail = (
                f"status={status} start={data['started_at']} end={data['ended_at']} "
                f"duration={data['duration_seconds']}s attempts={data['attempts']} "
                f"{counts} warnings={data['warnings']} errors={data['errors']} "
                f"needs_review={data['needs_review']} "
                f"next_action={data['next_action']} "
                f"schedule={data.get('schedule') or '-'} "
                f"last_check={data.get('last_successful_check') or '-'} "
                f"reason={data.get('reason') or '-'} last_error={data.get('last_error') or '-'} "
                f"artifact_id={data.get('artifact_id')}"
            )
        elif self.main and event in {"ATTEMPT_FAILED", "SOURCE_SETUP_FAILED"}:
            detail = (
                f"error_type={data.get('error_type')} error={data.get('error')} "
                f"details={self._error_label(record.job_run_id)}"
            )
        elif self.main and event == "JOB_STARTED":
            detail = (f"start={data['started_at']} sources={data['sources']} "
                      f"timezone={data['timezone']} errors={record.job_run_id}.errors.log "
                      f"events={record.job_run_id}.events.jsonl")
        line = (
            f"{stamp} | {record.levelname:<7} | "
            f"job={record.job_name} run={record.job_run_id} "
            f"source={record.source_name} attempt={record.attempt} "
            f"stage={record.stage} | {event} {detail}"
        )
        if not self.main and record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line

    @staticmethod
    def _error_label(run_id: str) -> str:
        return f"{run_id}.errors.log"


class _Structured(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp_utc": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "job": record.job_name,
            "job_run_id": record.job_run_id,
            "source": record.source_name,
            "attempt": record.attempt,
            "stage": record.stage,
            "event": getattr(record, "event", "LOG"),
            "logger": record.name,
            "message": record.getMessage(),
            "data": getattr(record, "job_data", {}),
        }
        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class JobLog:
    """Readable summaries, full diagnostic issues, and all structured events."""

    def __init__(self, job_name: str, log_dir: Path, timezone_name: str | None = None):
        if not job_name or "/" in job_name or "\\" in job_name:
            raise ValueError("job_name must be a simple folder name")
        self.job_name = job_name
        self.timezone_name = timezone_name or None
        self.zone = ZoneInfo(timezone_name) if timezone_name else None
        self.run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + uuid4().hex[:8]
        )
        folder = Path(log_dir) / job_name
        self.text_path = folder / f"{self.run_id}.job.log"
        self.error_path = folder / f"{self.run_id}.errors.log"
        self.json_path = folder / f"{self.run_id}.events.jsonl"
        self._handlers: list[logging.Handler] = []
        self._previous_level: int | None = None
        self._old_console: list[logging.Handler] = []
        self._issues: dict[str, dict[str, int]] = {}

    def localize(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Database timestamps must include timezone")
        return value.astimezone(self.zone).isoformat(timespec="seconds")

    def issues(self, source: str) -> dict[str, int]:
        return self._issues.get(source, {"warnings": 0, "errors": 0}).copy()

    def __enter__(self):
        self.text_path.parent.mkdir(parents=True, exist_ok=True)
        context = _JobContext(self.job_name, self.run_id)
        text = logging.FileHandler(self.text_path, encoding="utf-8")
        text.setFormatter(_Readable(self.zone, main=True))
        text.addFilter(_MainOnly())
        errors = logging.FileHandler(self.error_path, encoding="utf-8")
        errors.setFormatter(_Readable(self.zone))
        errors.addFilter(_IssueOnly(self._issues))
        structured = logging.FileHandler(self.json_path, encoding="utf-8")
        structured.setFormatter(_Structured())
        self._handlers = [text, errors, structured]
        root = logging.getLogger()
        self._previous_level = root.level
        self._old_console = [handler for handler in root.handlers
                             if isinstance(handler, logging.StreamHandler)
                             and not isinstance(handler, logging.FileHandler)]
        for handler in self._old_console:
            root.removeHandler(handler)
        root.setLevel(logging.INFO)
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(_Readable(self.zone, main=True))
        console.addFilter(_MainOnly())
        self._handlers.append(console)
        for handler in self._handlers:
            handler.addFilter(context)
            root.addHandler(handler)
        _quiet_dependencies()
        return self

    def __exit__(self, exc_type, exc, tb):
        root = logging.getLogger()
        for handler in self._handlers:
            root.removeHandler(handler)
            handler.close()
        for handler in self._old_console:
            root.addHandler(handler)
        if self._previous_level is not None:
            root.setLevel(self._previous_level)

    @contextmanager
    def bind(self, source: str = "-", attempt: int = 0, stage: str = "JOB"):
        """Add context even to logs emitted by existing pipeline modules."""
        tokens = (
            _source.set(source), _attempt.set(attempt), _stage.set(stage)
        )
        try:
            yield
        finally:
            _source.reset(tokens[0])
            _attempt.reset(tokens[1])
            _stage.reset(tokens[2])

    def stamp(self) -> str:
        return datetime.now(timezone.utc).astimezone(self.zone).isoformat(
            timespec="seconds"
        )

    def event(
        self, name: str, *, level: int = logging.INFO,
        error: Exception | None = None, **data
    ) -> None:
        """Route an event to the appropriate log files by event and severity."""
        if error is not None:
            data.update(error_type=type(error).__name__, error=str(error))
        details = " ".join(f"{key}={value}" for key, value in data.items())
        message = f"{name} {details}".rstrip()
        logging.getLogger("jobs").log(
            level, message,
            extra={"event": name, "job_data": data},
            exc_info=error is not None,
        )