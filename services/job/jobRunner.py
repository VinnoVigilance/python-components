"""Sequential, reusable source runner for watchlist and media jobs."""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from config.loggingConfig import JobLog


_DONE_STATUSES = {"SUCCESS", "UNCHANGED", "WAITING", "PARTIAL"}
_PENDING_STATUSES = {"WAITING", "PARTIAL"}
_CORE_COUNT_KEYS = (
    "core_new_count", "core_updated_count", "core_deleted_count", "core_skipped_count",
)


@dataclass(frozen=True)
class SourceOutcome:
    status: str  # SUCCESS, UNCHANGED, WAITING (held) or PARTIAL (some items pending)
    counts: Mapping[str, int] = field(default_factory=dict)
    artifact_id: int | None = None
    detail: str | None = None


def _new_counts() -> dict:
    return dict(
        success=0, unchanged=0, waiting=0, partial=0, not_due=0, failed=0, attempts=0,
        sources_with_issues=0, **{key: 0 for key in _CORE_COUNT_KEYS},
    )


def _pending_retry(config: dict, now: datetime, timezone_name: str | None) -> Any:
    return SimpleNamespace(should_run=True, reason="pending retry", last_successful_check=None)


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
    stage: str = "PIPELINE",
    rows: list | None = None,
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
            with log.bind(name, number, stage):
                log.event("ATTEMPT_STARTED", started_at=attempt_at,
                          max_attempts=max_attempts)
                try:
                    outcome = execute_fn(name)
                    if outcome.status not in _DONE_STATUSES:
                        raise ValueError(f"Unknown source outcome: {outcome.status!r}")
                    status = outcome.status
                    summary[status.lower()] += 1
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
            done = outcome is not None and status in _DONE_STATUSES
            next_action = (
                "fix_failure_using_errors_log" if status == "FAILED" else
                "pending_items_are_retried_by_the_job" if status in _PENDING_STATUSES else
                "review_recovered_issues" if needs_review else "none"
            )
            if needs_review and done:
                summary["sources_with_issues"] += 1
            # Successful pipeline output is authoritative for committed Core counts.
            if done:
                for key in _CORE_COUNT_KEYS:
                    summary[key] += outcome.counts.get(key, 0)
            log.event(
                "SOURCE_FINISHED", level=(logging.ERROR if status == "FAILED" else
                                          logging.WARNING if needs_review or
                                          status in _PENDING_STATUSES else logging.INFO),
                status=status, started_at=started_at,
                ended_at=log.stamp(),
                duration_seconds=round(time.perf_counter() - started, 2),
                attempts=attempts, artifact_id=outcome.artifact_id if done else None,
                reason=reason, last_error=last_error,
                schedule=config.get("schedule"),
                last_successful_check=last_successful_check,
                needs_review=needs_review, next_action=next_action, **issues,
                detail=outcome.detail if done else None,
                **(outcome.counts if done else {}),
            )
            if rows is not None:
                rows.append(dict(
                    name=name, stage=stage, status=status,
                    seconds=round(time.perf_counter() - started),
                    counts=dict(outcome.counts) if done else {},
                ))


def _run_retry_pass(
    pending_fn: Callable[[], Mapping[str, dict]] | None,
    retry_fn: Callable[[str], SourceOutcome] | None,
    log: JobLog,
    summary: dict,
    sleeper: Callable[[float], None],
    rows: list,
) -> None:
    """Retry every source that still has pending items, once per pass."""
    if pending_fn is None or retry_fn is None:
        return
    for name, config in pending_fn().items():
        _run_source(
            name, config, _pending_retry, retry_fn, log, summary,
            1, 0, sleeper, stage="RETRY", rows=rows,
        )


def _cell(value: Any) -> str:
    return "-" if value in (None, "") else f"{value:,}" if isinstance(value, int) else str(value)


def format_summary_table(rows: list[dict]) -> str:
    """One line per source run: what the source had, what we got, and what changed."""
    header = ("LIST", "PASS", "STATUS", "SOURCE", "GOT", "NEW", "UPD", "DEL",
              "SKIP", "FAILED", "PENDING", "SECS")
    lines = []
    for row in rows:
        counts = row["counts"]
        expected = counts.get("expected_detail_count")
        missing = counts.get("missing_detail_count")
        source = expected or counts.get("parsed_record_count") or counts.get("discovered_count")
        got = (
            expected - missing if expected is not None and missing is not None
            else counts.get("parsed_record_count") or counts.get("stored_count")
        )
        failed = missing or counts.get("broken_detail_count") or counts.get("failed_item_count")
        pending = missing if row["status"] == "WAITING" else counts.get("todo_pending_count")
        lines.append((
            row["name"], row["stage"].replace("PIPELINE", "MAIN"), row["status"],
            _cell(source), _cell(got), _cell(counts.get("core_new_count")),
            _cell(counts.get("core_updated_count")), _cell(counts.get("core_deleted_count")),
            _cell(counts.get("core_skipped_count")), _cell(failed), _cell(pending),
            _cell(row["seconds"]),
        ))
    widths = [max(len(str(item)) for item in column) for column in zip(header, *lines)]
    render = lambda values: "  ".join(str(v).ljust(w) for v, w in zip(values, widths))
    return "\n".join(
        ["", "RUN SUMMARY", render(header), render(["-" * w for w in widths]),
         *map(render, lines), ""]
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
    pending_fn: Callable[[], Mapping[str, dict]] | None = None,
    retry_fn: Callable[[str], SourceOutcome] | None = None,
    retry_before: bool = False,
) -> dict:
    """Run sources in config order; failed sources never block subsequent ones.

    With pending_fn/retry_fn, sources with pending items are retried at the end
    of the job, and also at the start when retry_before is set.
    """
    if max_attempts < 1 or retry_delay_seconds < 0:
        raise ValueError("Invalid retry settings")
    summary = dict(
        job_run_id=log.run_id, total=len(sources),
        log_path=str(log.text_path), error_log_path=str(log.error_path),
        json_log_path=str(log.json_path),
        **_new_counts(), retry=_new_counts(), still_pending=0,
    )
    rows: list[dict] = []
    summary["rows"] = rows
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
                if retry_before:
                    _run_retry_pass(pending_fn, retry_fn, log, summary["retry"], sleeper, rows)
                for name, config in sources.items():
                    _run_source(
                        name, config, decision_fn, execute_fn, log, summary,
                        max_attempts, retry_delay_seconds, sleeper, rows=rows,
                    )
                _run_retry_pass(pending_fn, retry_fn, log, summary["retry"], sleeper, rows)
                summary["still_pending"] = len(pending_fn()) if pending_fn else 0
            summary["status"] = (
                "COMPLETED_WITH_FAILURES" if summary["failed"] or summary["retry"]["failed"] else
                "COMPLETED_WITH_WARNINGS" if summary["sources_with_issues"] or
                summary["still_pending"] else "COMPLETED"
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
                                       summary.get("still_pending") or
                                       summary.get("status") == "ALREADY_RUNNING" else logging.INFO),
                status=summary.get("status", "INTERRUPTED"),
                started_at=started_at, ended_at=log.stamp(),
                duration_seconds=round(time.perf_counter() - started, 2),
                **{key: summary[key] for key in (
                    "total", "success", "unchanged", "waiting", "partial", "not_due",
                    "failed", "attempts", "sources_with_issues", "still_pending",
                    *_CORE_COUNT_KEYS,
                )},
                retry=summary["retry"],
            )
            if rows:
                log.write_block(format_summary_table(rows))
