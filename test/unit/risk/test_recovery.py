"""Verify failed-ID recovery without a database or model weights."""

from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock

import pytest

from pipelines.riskCategoryPipeline import _parse_args
from repositories import jobStateRepository
from services.risk import processing


def item(work_id, subject_id=5):
    return dict(id=work_id, subject_id=subject_id, source_record_id=work_id + 100,
                action="UPDATE")


def saved_item(work_id=2, effective_date="2026-10-02"):
    return dict(item=item(work_id), mode="incremental", effective_date=effective_date,
                source_name=None, attempts=3, error="failed", error_type="ValueError")


@pytest.fixture
def runtime(monkeypatch):
    repository = MagicMock()
    repository.latest_risk_date.return_value = date(2026, 10, 1)
    records = {date(2026, 10, 2): [item(2)], date(2026, 10, 20): [item(20)]}
    repository.find_scan_end.side_effect = lambda cursor, mode, day: max(
        (row["id"] for row in records.get(day, [])), default=0)
    repository.find_delta_batch.side_effect = lambda connection, day, after, end, size: [
        row for row in records.get(day, []) if after < row["id"] <= end
    ][:size]
    monkeypatch.setattr(processing, "advisory_job_lock", lambda _: nullcontext(True))
    monkeypatch.setattr(processing, "connection_pool", MagicMock())
    return repository, records


def test_only_failed_id_is_retried_without_rescanning_later_dates(runtime, monkeypatch):
    repository, _ = runtime
    broken = True
    visited = []

    def process(connection, repo, work, mode, classify):
        visited.append(work["id"])
        if work["id"] == 2 and broken:
            raise ValueError("temporary failure")
        return "skipped"

    monkeypatch.setattr(processing, "_process", process)
    result = processing.run("media", repository, 1, effective_date=date(2026, 10, 20))
    assert visited == [2, 2, 2, 20]
    assert result["run_status"] == "PARTIAL"
    state = jobStateRepository.load_state("risk", "media")
    assert state["next_date"] == "2026-10-21"
    assert set(state["items"]) == {"incremental:2"}
    assert state["items"]["incremental:2"]["item"] == item(2)
    assert state["items"]["incremental:2"]["effective_date"] == "2026-10-02"

    broken = False
    visited.clear()
    repository.find_delta_batch.reset_mock()
    result = processing.run("media", repository, 1, effective_date=date(2026, 10, 21))
    assert visited == [2]
    assert result["retried_count"] == 1
    assert result["pending_count"] == 0
    assert result["run_status"] == "SUCCESS"
    assert [day["effective_date"] for day in result["dates"]] == [date(2026, 10, 21)]
    repository.find_delta_batch.assert_not_called()
    assert jobStateRepository.load_state("risk", "media")["next_date"] == "2026-10-22"


def test_exhausted_retry_remains_queued_while_new_dates_advance(runtime, monkeypatch):
    repository, _ = runtime
    jobStateRepository.save_state("risk", "media", {
        "next_date": "2026-10-21", "items": {"incremental:2": saved_item()},
    })
    process = MagicMock(side_effect=ValueError("still broken"))
    monkeypatch.setattr(processing, "_process", process)
    result = processing.run("media", repository, 1, effective_date=date(2026, 10, 21))
    state = jobStateRepository.load_state("risk", "media")
    assert state["items"]["incremental:2"]["attempts"] == 6
    assert state["next_date"] == "2026-10-22"
    assert result["pending_count"] == 1
    assert result["run_status"] != "SUCCESS"
    assert process.call_count == 3


def test_interruption_preserves_only_the_interrupted_day(runtime, monkeypatch):
    repository, _ = runtime
    monkeypatch.setattr(processing, "_process", MagicMock(side_effect=processing.ClassifierLoadError("missing")))
    with pytest.raises(processing.ClassifierLoadError):
        processing.run("media", repository, 1, effective_date=date(2026, 10, 20))
    state = jobStateRepository.load_state("risk", "media")
    assert state["next_date"] == "2026-10-02"
    assert state["inflight_date"] == "2026-10-02"
    assert "replay_from" not in state
    process = MagicMock(return_value="skipped")
    monkeypatch.setattr(processing, "_process", process)
    processing.run("media", repository, 1, effective_date=date(2026, 10, 20))
    assert [call.args[2]["id"] for call in process.call_args_list] == [2, 20]
    assert "inflight_date" not in jobStateRepository.load_state("risk", "media")


def test_limited_day_retains_its_scan_marker(runtime, monkeypatch):
    repository, records = runtime
    records[date(2026, 10, 2)].append(item(3))
    monkeypatch.setattr(processing, "_process", MagicMock(return_value="skipped"))
    result = processing.run("media", repository, 1, effective_date=date(2026, 10, 20), max_rows=1)
    state = jobStateRepository.load_state("risk", "media")
    assert result["run_status"] == "LIMIT_REACHED"
    assert state["next_date"] == "2026-10-02"
    assert state["inflight_date"] == "2026-10-02"
    assert state["items"] == {}


def test_retry_uses_row_budget_before_new_scans(runtime, monkeypatch):
    repository, _ = runtime
    jobStateRepository.save_state("risk", "media", {
        "next_date": "2026-10-21", "items": {"incremental:2": saved_item()},
    })
    monkeypatch.setattr(processing, "_process", MagicMock(return_value="skipped"))
    result = processing.run("media", repository, 1, effective_date=date(2026, 10, 21), max_rows=1)
    assert result["retried_count"] == 1
    assert result["processed_count"] == 1
    assert result["run_status"] == "LIMIT_REACHED"
    assert jobStateRepository.load_state("risk", "media")["next_date"] == "2026-10-21"
    repository.find_scan_end.assert_not_called()


def test_future_pending_item_is_preserved_for_later_target(runtime, monkeypatch):
    repository, _ = runtime
    jobStateRepository.save_state("risk", "media", {
        "next_date": "2026-10-21", "items": {"incremental:22": saved_item(22, "2026-10-22")},
    })
    process = MagicMock()
    monkeypatch.setattr(processing, "_process", process)
    result = processing.run("media", repository, 1, effective_date=date(2026, 10, 21))
    process.assert_not_called()
    assert result["pending_count"] == 1
    assert result["run_status"] == "PARTIAL"


def test_queue_write_failure_cannot_advance_past_failed_date(runtime, monkeypatch):
    repository, _ = runtime
    save = jobStateRepository.save_state

    def fail_pending_write(kind, name, state):
        if state["items"]:
            raise OSError("disk unavailable")
        return save(kind, name, state)

    monkeypatch.setattr(jobStateRepository, "save_state", fail_pending_write)
    monkeypatch.setattr(processing, "_process", MagicMock(side_effect=ValueError("record failed")))
    with pytest.raises(OSError, match="disk unavailable"):
        processing.run("media", repository, 1, effective_date=date(2026, 10, 20))
    state = jobStateRepository.load_state("risk", "media")
    assert state["inflight_date"] == "2026-10-02"
    assert state["next_date"] == "2026-10-02"


def test_domain_queues_do_not_mix(runtime, monkeypatch):
    repository, _ = runtime
    jobStateRepository.save_state("risk", "watchlist", {"items": {"incremental:2": saved_item()}})
    process = MagicMock(return_value="skipped")
    monkeypatch.setattr(processing, "_process", process)
    result = processing.run("media", repository, 1, effective_date=date(2026, 10, 1))
    assert result["pending_count"] == 0
    assert "incremental:2" in jobStateRepository.load_state("risk", "watchlist")["items"]
    process.assert_not_called()


def test_empty_risk_database_requires_initial_without_seeding_state(runtime):
    repository, _ = runtime
    repository.latest_risk_date.return_value = None
    result = processing.run("media", repository, 1)
    assert result["run_status"] == "INITIAL_REQUIRED"
    assert jobStateRepository.load_state("risk", "media") is None
    repository.find_scan_end.assert_not_called()


def test_empty_days_advance_without_classifying(runtime):
    repository, records = runtime
    records.clear()
    classifier = MagicMock()
    result = processing.run("media", repository, 1, effective_date=date(2026, 10, 3), classifier=classifier)
    assert len(result["dates"]) == 3
    assert result["processed_count"] == 0
    assert jobStateRepository.load_state("risk", "media")["next_date"] == "2026-10-04"
    classifier.classify.assert_not_called()


@pytest.mark.parametrize("flag, mode", [("--initial", "initial"), ("--incremental", "incremental")])
def test_explicit_mode_aliases(flag, mode):
    assert _parse_args([flag]).mode == mode


def test_mode_aliases_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        _parse_args(["--initial", "--incremental"])


@pytest.mark.parametrize("failures", [1, 3])
def test_record_retries_are_bounded_and_counted_once(runtime, monkeypatch, failures):
    repository, _ = runtime
    process = MagicMock(side_effect=[ValueError("temporary")] * failures + ["skipped"])
    monkeypatch.setattr(processing, "_process", process)
    result = processing._scan(MagicMock(), repository, "media", "incremental",
                              date(2026, 10, 2), None, 10, None, MagicMock())
    assert result["processed_count"] == 1
    assert process.call_count == min(failures + 1, 3)
    assert result["failed_count"] == int(failures == 3)
