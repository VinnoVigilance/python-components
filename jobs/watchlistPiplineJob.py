"""Watchlist adapter for the shared source runner.

Run with:

    python -m jobs.watchlistPiplineJob
"""

import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from config.loggingConfig import JobLog
from infrastructure.database.jobLock import advisory_job_lock
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.job.jobRunner import SourceOutcome, run_sources
from services.watchlistPipeline.watchlistScheduleService import (
    should_run_today,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_JOB_LOCK_ID = 802156601

_COUNT_FIELDS = (
    "core_new_count",
    "core_updated_count",
    "core_deleted_count",
    "core_skipped_count",
    "core_processed_count",
    "parsed_record_count",
    "processed_record_count",
    "raw_record_count",
)


def _run_watchlist_worker(
    name: str,
) -> SourceOutcome:
    """
    Run one Watchlist pipeline inside a child Process.

    Each source receives a fresh Python Process, so Scrapy reactors,
    asyncio event loops and browser resources cannot affect the next source.
    """

    from config.loggingConfig import configure_logging
    from pipelines.watchlistPipeline import (
        run_watchlist_pipeline,
    )

    configure_logging()

    result = run_watchlist_pipeline(
        watchlist_name=name,
    )

    pipeline_result = result["pipeline_result"]

    if pipeline_result not in {
        "NORMALIZED",
        "SKIPPED",
    }:
        raise ValueError(
            "Unexpected pipeline result: "
            f"{pipeline_result!r}"
        )

    return SourceOutcome(
        status=(
            "UNCHANGED"
            if pipeline_result == "SKIPPED"
            else "SUCCESS"
        ),
        counts={
            key: result.get(key, 0)
            for key in _COUNT_FIELDS
        },
        artifact_id=result.get(
            "watchlist_file_id"
        ),
        detail=result.get(
            "duplicate_status"
        ),
    )


def _execute_watchlist(
    name: str,
) -> SourceOutcome:
    """
    Create one fresh OS Process for each source attempt.

    The executor is intentionally created inside this function so its
    worker is never reused for another source or retry.
    """

    process_context = (
        multiprocessing.get_context("spawn")
    )

    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=process_context,
    ) as executor:
        future = executor.submit(
            _run_watchlist_worker,
            name,
        )

        return future.result()


def run_watchlist_job(
    configs: dict | None = None,
) -> dict:
    timezone_name = (
        os.getenv("WATCHLIST_JOB_TIMEZONE")
        or None
    )

    log = JobLog(
        "watchlist",
        PROJECT_ROOT / "logs",
        timezone_name,
    )

    return run_sources(
        sources=(
            WATCHLIST_CONFIGS
            if configs is None
            else configs
        ),
        decision_fn=should_run_today,
        execute_fn=_execute_watchlist,
        log=log,
        lock=partial(
            advisory_job_lock,
            _JOB_LOCK_ID,
        ),
        max_attempts=3,
        retry_delay_seconds=5,
    )


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(
            PROJECT_ROOT / ".env"
        )

    except ImportError:
        pass

    try:
        result = run_watchlist_job()

    except Exception:
        # The shared runner writes JOB_ABORTED and
        # the traceback to the error log.
        return 2

    return (
        1
        if result["failed"]
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())