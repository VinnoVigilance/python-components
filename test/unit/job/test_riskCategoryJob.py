"""Verify combined risk execution without database access or model loading."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobs import riskCategoryJob as job
from config.loggingConfig import JobLog


@pytest.fixture
def dispatch(monkeypatch, tmp_path):
    monkeypatch.setattr(job, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("WATCHLIST_JOB_TIMEZONE", raising=False)
    run = MagicMock(return_value=dict(run_status="SUCCESS", processed_count=2,
                                     failed_count=0, pending_count=0, retried_count=1))
    monkeypatch.setattr(job, "run_risk_category_etl", run)
    return run


def test_default_runs_both_domains_and_records_summary(dispatch):
    result = job.run_risk_job()
    assert [call.kwargs["subject"] for call in dispatch.call_args_list] == ["media", "watchlist"]
    assert all(call.kwargs["mode"] == "incremental" for call in dispatch.call_args_list)
    assert result["status"] == "SUCCESS"
    assert result["failed"] == 0
    assert "RISK SUMMARY" in Path(result["log_path"]).read_text(encoding="utf-8")


def test_media_exception_does_not_prevent_watchlist_and_is_logged(dispatch):
    dispatch.side_effect = [RuntimeError("media unavailable"), dict(run_status="SUCCESS")]
    result = job.run_risk_job()
    assert dispatch.call_count == 2
    assert result["status"] == "PARTIAL"
    assert result["failed"] == 1
    assert result["domains"]["media"]["error_type"] == "RuntimeError"
    assert "media unavailable" in Path(result["error_log_path"]).read_text(encoding="utf-8")


def test_pending_or_initial_required_is_not_success(dispatch):
    dispatch.side_effect = [dict(run_status="INITIAL_REQUIRED", no_work_reason="Run INITIAL"),
                            dict(run_status="PARTIAL", pending_count=1)]
    result = job.run_risk_job()
    assert result["status"] == "FAILED"
    assert result["failed"] == 2
    assert "Run INITIAL" in Path(result["log_path"]).read_text(encoding="utf-8")


def test_selected_domain_only(dispatch):
    result = job.run_risk_job(mode="initial", subject="watchlist", max_rows=10)
    dispatch.assert_called_once_with(subject="watchlist", mode="initial", effective_date=None,
                                     batch_size=1000, max_rows=10, threads=4)
    assert set(result["domains"]) == {"watchlist"}


def test_interruption_aborts_without_starting_other_domain(dispatch):
    dispatch.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        job.run_risk_job()
    assert dispatch.call_count == 1


@pytest.mark.parametrize("flag, mode", [("--initial", "initial"), ("--incremental", "incremental")])
def test_cli_flags_dispatch_both_domains(monkeypatch, flag, mode):
    run = MagicMock(return_value={"status": "SUCCESS"})
    monkeypatch.setattr(job, "run_risk_job", run)
    assert job.main([flag]) == 0
    assert run.call_args.kwargs["mode"] == mode
    assert run.call_args.kwargs["subject"] == "both"


def test_cli_partial_result_exits_nonzero(monkeypatch):
    monkeypatch.setattr(job, "run_risk_job", lambda **kwargs: {"status": "PARTIAL"})
    assert job.main(["--incremental"]) == 2


def test_initial_rejects_delta_date_before_dispatch(dispatch):
    with pytest.raises(SystemExit) as error:
        job.main(["--initial", "--effective-date", "2026-10-20"])
    assert error.value.code == 2
    dispatch.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_real_logging_renders_risk_events(dispatch, capsys, fails):
    if fails:
        dispatch.side_effect = RuntimeError("classifier unavailable")
    result = job.run_risk_job(subject="media")
    captured = capsys.readouterr()
    assert "--- Logging error ---" not in captured.err
    events = [json.loads(line) for line in Path(result["json_log_path"]).read_text().splitlines()]
    lifecycle = [entry for entry in events if entry["event"].startswith("RISK_")]
    assert [entry["event"] for entry in lifecycle] == [
        "RISK_JOB_STARTED", "RISK_DOMAIN_STARTED", "RISK_DOMAIN_FINISHED", "RISK_JOB_FINISHED",
    ]
    finished = lifecycle[2]["data"]
    assert finished["status"] == ("FAILED" if fails else "SUCCESS")
    assert finished["started_at"] and finished["ended_at"]
    assert finished["duration_seconds"] >= 0
    text = Path(result["log_path"]).read_text()
    assert "RISK_DOMAIN_FINISHED" in text
    assert ("processed=unknown" if fails else "processed=2") in text
    assert ("status=FAILED" if fails else "status=SUCCESS") in captured.out


def test_incomplete_events_and_unknown_events_keep_logging(tmp_path, capsys):
    log = JobLog("risk", tmp_path)
    with log:
        log.event("RISK_DOMAIN_FINISHED", status="SUCCESS", processed=0)
        log.event("JOB_STARTED", mode="initial")
        log.event("SOURCE_FINISHED", status="SUCCESS", processed=2)
        log.event("CUSTOM_EVENT", extra_detail="preserved")
    captured = capsys.readouterr()
    assert "--- Logging error ---" not in captured.err
    text = log.text_path.read_text()
    assert "duration_seconds=unknown" in text
    assert "processed=0" in text
    assert "JOB_STARTED mode=initial" in text
    assert "SOURCE_FINISHED status=SUCCESS processed=2" in text
    assert "CUSTOM_EVENT extra_detail=preserved" in log.full_path.read_text()
