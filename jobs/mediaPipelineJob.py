"""Adverse Media adapter for the shared source runner.

Run with:

    python -m jobs.mediaPipelineJob
    python -m jobs.mediaPipelineJob --mode INITIAL --source SEC_PH_ADVISORIES
    python -m jobs.mediaPipelineJob --retry-only
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
from pipelines.mediaPipeline import load_media_config
from repositories import jobStateRepository
from services.job.jobRunner import SourceOutcome, run_sources


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_JOB_LOCK_ID = 802156602
_TODO_KIND = "media"
_JOB_MODES = ("INCREMENTAL", "INITIAL")

_COUNT_FIELDS = {
    "discovered_count": "discovered_count",
    "known_count": "known_count",
    "new_count": "new_count",
    "stored_count": "stored_count",
    "core_inserted_count": "core_new_count",
    "core_skipped_count": "core_skipped_count",
    "total_failed_count": "failed_item_count",
    "todo_pending_count": "todo_pending_count",
}


def _run_media_worker(
    name: str,
    mode: str,
    log_paths: tuple[str, str] | None = None,
) -> SourceOutcome:
    """Run one Media dataset inside a child Process (fresh Scrapy reactor)."""

    from config.loggingConfig import attach_child_job_logs, configure_logging
    from pipelines.mediaPipeline import run_media_pipeline

    configure_logging()

    if log_paths:
        attach_child_job_logs(*log_paths, name)

    result = run_media_pipeline(
        dataset_name=name,
        mode=mode,
    )

    run_status = result["run_status"]

    if run_status == "FAILED":
        raise RuntimeError(
            f"Media run failed: {result.get('failure_summary')}"
        )

    return SourceOutcome(
        status=run_status,
        counts={
            target: int(result.get(source, 0) or 0)
            for source, target in _COUNT_FIELDS.items()
        },
        detail=(
            f"mode={mode} stop_reason={result.get('stop_reason')} "
            f"failures={result.get('failure_summary') or {}}"
        ),
    )


def _execute_media(
    name: str,
    mode: str,
    log_paths: tuple[str, str] | None = None,
) -> SourceOutcome:
    """Create one fresh OS Process for each dataset attempt."""

    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=multiprocessing.get_context("spawn"),
    ) as executor:
        return executor.submit(
            _run_media_worker,
            name,
            mode,
            log_paths,
        ).result()


def _always_due(config: dict, now, timezone_name) -> SimpleNamespace:
    return SimpleNamespace(
        should_run=True,
        reason="media runs every time the job starts",
        last_successful_check=None,
    )


def _enabled_sources() -> dict:
    return {
        name: config
        for name, config in load_media_config().get("sources", {}).items()
        if config.get("enabled", True)
    }


def run_media_job(
    mode: str = "INCREMENTAL",
    dataset_names: list[str] | None = None,
    retry_only: bool = False,
) -> dict:
    enabled = _enabled_sources()
    sources = (
        enabled
        if not dataset_names
        else {name: enabled[name] for name in dataset_names}
    )

    def todo_sources() -> dict:
        return {
            name: sources[name]
            for name in jobStateRepository.list_state_names(_TODO_KIND)
            if name in sources
        }

    log = JobLog(
        "media",
        PROJECT_ROOT / "logs",
        os.getenv("WATCHLIST_JOB_TIMEZONE") or None,
    )

    log_paths = (str(log.full_path), str(log.error_path))

    result = run_sources(
        sources={} if retry_only else sources,
        decision_fn=_always_due,
        execute_fn=partial(_execute_media, mode=mode, log_paths=log_paths),
        log=log,
        lock=partial(advisory_job_lock, _JOB_LOCK_ID),
        max_attempts=3,
        retry_delay_seconds=5,
        pending_fn=todo_sources,
        retry_fn=partial(_execute_media, mode="RETRY", log_paths=log_paths),
        retry_before=not retry_only,
    )

    if jobStateRepository.export_pending_csv(_TODO_KIND, log.pending_path):
        result["pending_csv_path"] = str(log.pending_path)

    return result


def main(
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Adverse Media job.",
    )
    parser.add_argument(
        "--mode",
        type=str.upper,
        choices=_JOB_MODES,
        default="INCREMENTAL",
    )
    parser.add_argument(
        "--source",
        nargs="+",
        metavar="DATASET",
        help="Dataset names from config/mediaSources.yaml (default: all enabled).",
    )
    parser.add_argument(
        "--retry-only",
        action="store_true",
        help="Only retry the to-do list; no new discovery.",
    )
    arguments = parser.parse_args(argv)

    unknown = sorted(
        set(arguments.source or [])
        - set(_enabled_sources())
    )

    if unknown:
        parser.error("Unknown or disabled dataset(s): " + ", ".join(unknown))

    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")

    except ImportError:
        pass

    try:
        result = run_media_job(
            mode=arguments.mode,
            dataset_names=arguments.source,
            retry_only=arguments.retry_only,
        )

    except Exception:
        # The shared runner writes JOB_ABORTED and the traceback to the error log.
        return 2

    return 1 if result["failed"] or result["retry"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
