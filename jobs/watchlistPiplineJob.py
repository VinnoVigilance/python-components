"""Watchlist adapter for the shared source runner: python -m jobs.watchlistPiplineJob."""

import os
from functools import partial
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.loggingConfig import JobLog
from infrastructure.database.jobLock import advisory_job_lock
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.job.jobRunner import SourceOutcome, run_sources
from services.watchlistPipeline.watchlistScheduleService import should_run_today


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_JOB_LOCK_ID = 802156601
_COUNT_FIELDS = (
    "core_new_count", "core_updated_count", "core_deleted_count",
    "core_skipped_count", "core_processed_count", "parsed_record_count",
    "processed_record_count", "raw_record_count",
)


def _execute_watchlist(name: str) -> SourceOutcome:
    from pipelines.watchlistPipeline import run_watchlist_pipeline

    result = run_watchlist_pipeline(name)
    if result["pipeline_result"] not in {"NORMALIZED", "SKIPPED"}:
        raise ValueError(f"Unexpected pipeline result: {result['pipeline_result']!r}")
    return SourceOutcome(
        status="UNCHANGED" if result["pipeline_result"] == "SKIPPED" else "SUCCESS",
        counts={key: result.get(key, 0) for key in _COUNT_FIELDS},
        artifact_id=result.get("watchlist_file_id"),
        detail=result.get("duplicate_status"),
    )


def run_watchlist_job(configs: dict | None = None) -> dict:
    timezone_name = os.getenv("WATCHLIST_JOB_TIMEZONE") or None
    log = JobLog("watchlist", PROJECT_ROOT / "logs", timezone_name)
    return run_sources(
        sources=WATCHLIST_CONFIGS if configs is None else configs,
        decision_fn=should_run_today,
        execute_fn=_execute_watchlist,
        log=log,
        lock=partial(advisory_job_lock, _JOB_LOCK_ID),
        max_attempts=3,
        retry_delay_seconds=5,
    )


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass
    try:
        result = run_watchlist_job()
    except Exception:
        # The shared runner wrote JOB_ABORTED and the traceback to the error log.
        return 2
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())