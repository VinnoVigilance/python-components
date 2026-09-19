"""Sequential, reusable source runner for watchlist and media jobs."""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from config.loggingConfig import JobLog


@dataclass(frozen=True)
class SourceOutcome:
    status: str  # SUCCESS or UNCHANGED
    counts: Mapping[str, int] = field(default_factory=dict)
    artifact_id: int | None = None
    detail: str | None = None


def _run_source(
    name: str,
    config: dict,
    decision_fn: Callable[[dict, datetime, str | None], Any],
    execute_fn: Callable[[str], SourceOutcome],
    log: JobLog,
    summary: dict,
    max_attempts: int,
    retry_delay_seconds: float,
    sleeper: Callable[[float], None],
) -> None:
    started = time.perf_counter()
    started_at = log.stamp()
    status = "FAILED"
    attempts = 0
    outcome: SourceOutcome | None = None
    reason: str | None = None
    last_error: str | None = None
    last_successful_check: str | None = None

    try:
        with log.bind(name, stage="SCHEDULE"):
            log.event("SOURCE_CHECK_STARTED", started_at=started_at,
                      schedule=config.get("schedule"))
            decision = decision_fn(config, datetime.now(timezone.utc), log.timezone_name)
            reason = decision.reason
            last_successful_check = log.localize(decision.last_successful_check)
            if not decision.should_run:
                summary["not_due"] += 1
                status = "NOT_DUE"
                log.event("SOURCE_NOT_DUE", reason=decision.reason,
                          last_successful_check=last_successful_check)
                return
            log.event("SOURCE_DUE", reason=decision.reason,
                      last_successful_check=last_successful_check)

        for number in range(1, max_attempts + 1):
            attempts += 1
            summary["attempts"] += 1
            attempt_started = time.perf_counter()
            attempt_at = log.stamp()
            with log.bind(name, number, "PIPELINE"):
                log.event("ATTEMPT_STARTED", started_at=attempt_at,
                          max_attempts=max_attempts)
                try:
                    outcome = execute_fn(name)
                    if outcome.status not in {"SUCCESS", "UNCHANGED"}:
                        raise ValueError(f"Unknown source outcome: {outcome.status!r}")
                    status = outcome.status
                    summary["unchanged" if status == "UNCHANGED" else "success"] += 1
                    log.event(
                        "ATTEMPT_FINISHED", status=status,
                        started_at=attempt_at, ended_at=log.stamp(),
                        duration_seconds=round(time.perf_counter() - attempt_started, 2),
                        artifact_id=outcome.artifact_id, detail=outcome.detail,
                        **outcome.counts,
                    )
                    break
                except Exception as error:
                    last_error = f"{type(error).__name__}: {error}"
                    log.event(
                        "ATTEMPT_FAILED", level=logging.ERROR, error=error,
                        started_at=attempt_at, ended_at=log.stamp(),
                        duration_seconds=round(time.perf_counter() - attempt_started, 2),
                    )
                    if number == max_attempts:
                        summary["failed"] += 1
                    else:
                        delay = retry_delay_seconds * 2 ** (number - 1)
                        log.event("RETRY_WAIT", level=logging.WARNING,
                                  seconds=delay, next_attempt=number + 1)
                        sleeper(delay)
    except Exception as error:
        # Schedule/lookup failures cannot be fixed by retrying the pipeline.
        last_error = f"{type(error).__name__}: {error}"
        summary["failed"] += 1
        with log.bind(name, attempts, "SCHEDULE" if attempts == 0 else "JOB"):
            log.event("SOURCE_SETUP_FAILED", level=logging.ERROR, error=error)
    finally:
        with log.bind(name, attempts, "SUMMARY"):
            issues = log.issues(name)
            needs_review = bool(issues["warnings"] or issues["errors"])
            next_action = (
                "fix_failure_using_errors_log" if status == "FAILED" else
                "review_recovered_issues" if needs_review else "none"
            )
            if needs_review and status in {"SUCCESS", "UNCHANGED"}:
                summary["sources_with_issues"] += 1
            # Successful pipeline output is authoritative for committed Core counts.
            if outcome and status in {"SUCCESS", "UNCHANGED"}:
                for key in ("core_new_count", "core_updated_count", "core_deleted_count", "core_skipped_count"):
                    summary[key] += outcome.counts.get(key, 0)
            log.event(
                "SOURCE_FINISHED", level=(logging.ERROR if status == "FAILED" else
                                          logging.WARNING if needs_review else logging.INFO),
                status=status, started_at=started_at,
                ended_at=log.stamp(),
                duration_seconds=round(time.perf_counter() - started, 2),
                attempts=attempts, artifact_id=outcome.artifact_id if outcome and
                status in {"SUCCESS", "UNCHANGED"} else None,
                reason=reason, last_error=last_error,
                schedule=config.get("schedule"),
                last_successful_check=last_successful_check,
                needs_review=needs_review, next_action=next_action, **issues,
                **(outcome.counts if outcome and status in {"SUCCESS", "UNCHANGED"} else {}),
            )


def run_sources(
    *,
    sources: Mapping[str, dict],
    decision_fn: Callable[[dict, datetime, str | None], Any],
    execute_fn: Callable[[str], SourceOutcome],
    log: JobLog,
    lock: Callable[[], Any],
    max_attempts: int = 3,
    retry_delay_seconds: float = 5,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    """Run sources in config order; failed sources never block subsequent ones."""
    if max_attempts < 1 or retry_delay_seconds < 0:
        raise ValueError("Invalid retry settings")
    summary = dict(
        job_run_id=log.run_id, total=len(sources), success=0,
        unchanged=0, not_due=0, failed=0, attempts=0,
        log_path=str(log.text_path), error_log_path=str(log.error_path),
        json_log_path=str(log.json_path),
        core_new_count=0, core_updated_count=0, core_deleted_count=0,
        core_skipped_count=0, sources_with_issues=0,
    )
    with log:
        started = time.perf_counter()
        started_at = log.stamp()
        log.event("JOB_STARTED", started_at=started_at,
                  sources=len(sources), timezone=log.timezone_name or "SYSTEM_LOCAL",
                  errors_file=str(log.error_path), events_file=str(log.json_path))
        try:
            with lock() as acquired:
                if not acquired:
                    summary["status"] = "ALREADY_RUNNING"
                    log.event("JOB_ALREADY_RUNNING", level=logging.WARNING)
                    return summary
                for name, config in sources.items():
                    _run_source(
                        name, config, decision_fn, execute_fn, log, summary,
                        max_attempts, retry_delay_seconds, sleeper,
                    )
            summary["status"] = (
                "COMPLETED_WITH_FAILURES" if summary["failed"] else
                "COMPLETED_WITH_WARNINGS" if summary["sources_with_issues"] else "COMPLETED"
            )
            return summary
        except Exception as error:
            summary["status"] = "ABORTED"
            log.event("JOB_ABORTED", level=logging.ERROR, error=error)
            raise
        finally:
            log.event(
                "JOB_FINISHED", level=(logging.ERROR if summary.get("status") == "ABORTED" else
                                       logging.WARNING if summary.get("failed") or
                                       summary.get("sources_with_issues") or
                                       summary.get("status") == "ALREADY_RUNNING" else logging.INFO),
                status=summary.get("status", "INTERRUPTED"),
                started_at=started_at, ended_at=log.stamp(),
                duration_seconds=round(time.perf_counter() - started, 2),
                **{key: summary[key] for key in (
                    "total", "success", "unchanged", "not_due", "failed", "attempts",
                    "sources_with_issues",
                    "core_new_count", "core_updated_count", "core_deleted_count",
                    "core_skipped_count",
                )},
            )