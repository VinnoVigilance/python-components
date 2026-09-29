"""Unit tests for services/job/jobRunner.py (statuses, retry passes, summary table)."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from config.loggingConfig import JobLog
from services.job.jobRunner import SourceOutcome, format_summary_table, run_sources

pytestmark = pytest.mark.unit


def _due(config, now, timezone_name):
    return SimpleNamespace(should_run=True, reason="due", last_successful_check=None)


def _run(tmp_path, execute_fn, sources=None, **kwargs):
    return run_sources(
        sources={"A": {}, "B": {}} if sources is None else sources,
        decision_fn=_due,
        execute_fn=execute_fn,
        log=JobLog("test", tmp_path / "logs"),
        lock=lambda: nullcontext(True),
        max_attempts=2,
        retry_delay_seconds=0,
        sleeper=lambda seconds: None,
        **kwargs,
    )


def test_waiting_and_partial_count_as_done_not_failed(tmp_path):
    outcomes = {
        "A": SourceOutcome(status="WAITING", counts={"core_new_count": 0}),
        "B": SourceOutcome(status="PARTIAL", counts={"core_new_count": 4}),
    }

    result = _run(tmp_path, outcomes.__getitem__)

    assert (result["waiting"], result["partial"], result["failed"]) == (1, 1, 0)
    assert result["attempts"] == 2
    assert result["core_new_count"] == 4
    assert result["status"] == "COMPLETED"


def test_unknown_status_is_a_failure(tmp_path):
    result = _run(tmp_path, lambda name: SourceOutcome(status="WEIRD"), sources={"A": {}})

    assert result["failed"] == 1
    assert result["status"] == "COMPLETED_WITH_FAILURES"


def test_pending_sources_are_retried_at_the_end(tmp_path):
    calls = []
    pending = {"A": {}}

    def execute(name):
        calls.append(("main", name))
        return SourceOutcome(status="WAITING")

    def retry(name):
        calls.append(("retry", name))
        pending.clear()
        return SourceOutcome(status="SUCCESS")

    result = _run(tmp_path, execute, sources={"A": {}}, pending_fn=lambda: dict(pending), retry_fn=retry)

    assert calls == [("main", "A"), ("retry", "A")]
    assert result["retry"]["success"] == 1
    assert result["still_pending"] == 0
    assert result["status"] == "COMPLETED"
    assert [(row["name"], row["stage"]) for row in result["rows"]] == [
        ("A", "PIPELINE"), ("A", "RETRY"),
    ]


def test_retry_before_runs_pending_sources_first(tmp_path):
    calls = []

    result = _run(
        tmp_path,
        lambda name: calls.append(("main", name)) or SourceOutcome(status="SUCCESS"),
        sources={"A": {}},
        pending_fn=lambda: {"X": {}},
        retry_fn=lambda name: calls.append(("retry", name)) or SourceOutcome(status="PARTIAL"),
        retry_before=True,
    )

    assert calls == [("retry", "X"), ("main", "A"), ("retry", "X")]
    assert result["still_pending"] == 1
    assert result["status"] == "COMPLETED_WITH_WARNINGS"


def test_failed_retry_marks_the_job_failed(tmp_path):
    def retry(name):
        raise RuntimeError("still down")

    result = _run(tmp_path, lambda name: SourceOutcome(status="SUCCESS"), sources={},
                  pending_fn=lambda: {"X": {}}, retry_fn=retry)

    assert result["retry"]["failed"] == 1
    assert result["retry"]["attempts"] == 1
    assert result["status"] == "COMPLETED_WITH_FAILURES"


def test_summary_table_is_written_to_the_job_log(tmp_path):
    result = _run(tmp_path, lambda name: SourceOutcome(status="SUCCESS"), sources={"A": {}})

    text = open(result["log_path"], encoding="utf-8").read()
    assert "RUN SUMMARY" in text


def test_summary_table_shows_source_vs_got():
    table = format_summary_table([
        dict(name="NCA", stage="PIPELINE", status="WAITING", seconds=3,
             counts={"expected_detail_count": 20, "missing_detail_count": 2}),
        dict(name="SEC", stage="RETRY", status="PARTIAL", seconds=1500,
             counts={"discovered_count": 50, "stored_count": 48, "core_new_count": 48,
                     "failed_item_count": 2, "todo_pending_count": 2}),
    ])

    lines = table.strip().splitlines()
    assert lines[1].split() == ["LIST", "PASS", "STATUS", "SOURCE", "GOT", "NEW", "UPD",
                                "DEL", "SKIP", "FAILED", "PENDING", "SECS"]
    assert lines[3].split() == ["NCA", "MAIN", "WAITING", "20", "18", "-", "-", "-", "-",
                                "2", "2", "3"]
    assert lines[4].split() == ["SEC", "RETRY", "PARTIAL", "50", "48", "48", "-", "-", "-",
                                "2", "2", "1,500"]
