"""Watchlist adapter for the shared source runner.

Run with:

    python -m jobs.watchlistPiplineJob
    python -m jobs.watchlistPiplineJob --source CFTC-RED-LIST
    python -m jobs.watchlistPiplineJob --retry-only
"""

import argparse
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from types import SimpleNamespace


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from config.loggingConfig import JobLog
from infrastructure.database.jobLock import advisory_job_lock
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from repositories import jobStateRepository
from services.job.jobRunner import SourceOutcome, run_sources
from services.watchlistPipeline.watchlistScheduleService import (
    should_run_today,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_JOB_LOCK_ID = 802156601
_HOLD_KIND = "watchlist"

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
    retry_held: bool = False,
    log_paths: tuple[str, str] | None = None,
) -> SourceOutcome:
    """
    Run one Watchlist pipeline inside a child Process.

    Each source receives a fresh Python Process, so Scrapy reactors,
    asyncio event loops and browser resources cannot affect the next source.
    """

    from config.loggingConfig import (
        attach_child_job_logs,
        configure_logging,
    )
    from pipelines.watchlistPipeline import (
        retry_held_watchlist,
        run_watchlist_pipeline,
    )
    from services.watchlistPipeline.watchlistFileService import (
        CrawlOnHold,
    )

    configure_logging()

    if log_paths:
        attach_child_job_logs(*log_paths, name)

    try:
        result = (
            retry_held_watchlist(name)
            if retry_held
            else run_watchlist_pipeline(watchlist_name=name)
        )

    except CrawlOnHold as hold:
        return SourceOutcome(
            status="WAITING",
            counts={
                "expected_detail_count": hold.expected_count,
                "missing_detail_count": len(hold.missing),
            },
            detail=str(hold),
        )

    if result is None:
        return SourceOutcome(
            status="UNCHANGED",
            detail="nothing held",
        )

    pipeline_result = result["pipeline_result"]
    broken = result.get("broken_detail_urls") or []

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
            **{
                key: result.get(key, 0)
                for key in _COUNT_FIELDS
            },
            "broken_detail_count": len(broken),
        },
        artifact_id=result.get(
            "watchlist_file_id"
        ),
        detail=(
            f"{result.get('duplicate_status')}; {len(broken)} detail "
            f"pages broken on the source site: {', '.join(broken)}"
            if broken
            else result.get("duplicate_status")
        ),
    )


def _execute_watchlist(
    name: str,
    retry_held: bool = False,
    log_paths: tuple[str, str] | None = None,
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
            retry_held,
            log_paths,
        )

        return future.result()


def run_watchlist_job(
    configs: dict | None = None,
    run_now: bool = False,
    retry_only: bool = False,
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

    sources = (
        WATCHLIST_CONFIGS
        if configs is None
        else configs
    )

    def held_sources() -> dict:
        return {
            name: sources[name]
            for name in jobStateRepository.list_state_names(
                _HOLD_KIND
            )
            if name in sources
        }

    log_paths = (
        str(log.full_path),
        str(log.error_path),
    )

    result = run_sources(
        sources={} if retry_only else sources,
        decision_fn=_run_now if run_now else should_run_today,
        execute_fn=partial(
            _execute_watchlist,
            log_paths=log_paths,
        ),
        log=log,
        lock=partial(
            advisory_job_lock,
            _JOB_LOCK_ID,
        ),
        max_attempts=3,
        retry_delay_seconds=5,
        pending_fn=held_sources,
        retry_fn=partial(
            _execute_watchlist,
            retry_held=True,
            log_paths=log_paths,
        ),
    )

    if jobStateRepository.export_pending_csv(_HOLD_KIND, log.pending_path):
        result["pending_csv_path"] = str(log.pending_path)

    return result


def _run_now(config: dict, now, timezone_name) -> SimpleNamespace:
    return SimpleNamespace(
        should_run=True,
        reason="requested by hand",
        last_successful_check=None,
    )


def main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Run the watchlist job.",
    )
    parser.add_argument(
        "--source",
        nargs="+",
        metavar="WATCHLIST",
        help="Run only these lists, right now (ignores the schedule).",
    )
    parser.add_argument(
        "--retry-only",
        action="store_true",
        help="Only retry held lists (missing detail pages); no new downloads.",
    )
    arguments = parser.parse_args(argv)

    unknown = sorted(
        set(arguments.source or [])
        - set(WATCHLIST_CONFIGS)
    )

    if unknown:
        parser.error("Unknown watchlist(s): " + ", ".join(unknown))

    try:
        from dotenv import load_dotenv

        load_dotenv(
            PROJECT_ROOT / ".env"
        )

    except ImportError:
        pass

    try:
        result = run_watchlist_job(
            configs=(
                {name: WATCHLIST_CONFIGS[name] for name in arguments.source}
                if arguments.source
                else None
            ),
            run_now=bool(arguments.source),
            retry_only=arguments.retry_only,
        )

    except Exception:
        # The shared runner writes JOB_ABORTED and
        # the traceback to the error log.
        return 2

    return (
        1
        if result["failed"] or result["retry"]["failed"]
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())