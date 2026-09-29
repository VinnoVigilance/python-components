"""Unit tests for jobs/watchlistPiplineJob.py (hold -> WAITING, retry pass, CLI flags)."""

import os
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

import config.loggingConfig as loggingConfig  # noqa: E402
import jobs.watchlistPiplineJob as job  # noqa: E402
import pipelines.watchlistPipeline as wp  # noqa: E402
from repositories import jobStateRepository  # noqa: E402
from services.job.jobRunner import SourceOutcome  # noqa: E402
from services.watchlistPipeline.watchlistFileService import CrawlOnHold  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_console_logging(monkeypatch):
    monkeypatch.setattr(loggingConfig, "configure_logging", lambda: None)


class TestWorker:
    def test_crawl_on_hold_becomes_waiting(self, monkeypatch):
        missing = [{"detail_url": "https://x/2"}, {"detail_url": "https://x/3"}]
        monkeypatch.setattr(wp, "run_watchlist_pipeline", MagicMock(
            side_effect=CrawlOnHold("NCA", 10, missing)
        ))

        outcome = job._run_watchlist_worker("NCA")

        assert outcome.status == "WAITING"
        assert outcome.counts == {"expected_detail_count": 10, "missing_detail_count": 2}
        assert "read 8 of 10" in outcome.detail

    def test_broken_pages_are_reported_in_detail(self, monkeypatch):
        monkeypatch.setattr(wp, "run_watchlist_pipeline", MagicMock(return_value={
            "pipeline_result": "NORMALIZED",
            "duplicate_status": "NEW_VERSION",
            "core_new_count": 3,
            "broken_detail_urls": ["https://x/9"],
        }))

        outcome = job._run_watchlist_worker("CFTC")

        assert outcome.status == "SUCCESS"
        assert outcome.counts["core_new_count"] == 3
        assert outcome.counts["broken_detail_count"] == 1
        assert outcome.detail == "NEW_VERSION; 1 detail pages broken on the source site: https://x/9"

    def test_retry_uses_retry_held_watchlist(self, monkeypatch):
        retry = MagicMock(return_value={"pipeline_result": "SKIPPED", "duplicate_status": "DUP"})
        monkeypatch.setattr(wp, "retry_held_watchlist", retry)

        outcome = job._run_watchlist_worker("NCA", retry_held=True)

        retry.assert_called_once_with("NCA")
        assert outcome.status == "UNCHANGED"

    def test_retry_with_nothing_held_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(wp, "retry_held_watchlist", MagicMock(return_value=None))

        assert job._run_watchlist_worker("NCA", retry_held=True).status == "UNCHANGED"


class TestJob:
    @pytest.fixture
    def execute(self, monkeypatch, tmp_path):
        calls = []

        def fake_execute(name, retry_held=False, log_paths=None):
            calls.append((name, retry_held))
            if retry_held:
                jobStateRepository.delete_state("watchlist", name)
            return SourceOutcome(status="SUCCESS")

        monkeypatch.setattr(job, "_execute_watchlist", fake_execute)
        monkeypatch.setattr(job, "advisory_job_lock", lambda lock_id: nullcontext(True))
        monkeypatch.setattr(job, "PROJECT_ROOT", tmp_path)
        return calls

    def test_run_now_runs_chosen_lists_and_retries_held_ones(self, execute):
        jobStateRepository.save_state("watchlist", "B", {"missing": [{"record_id": "1"}]})

        result = job.run_watchlist_job(configs={"A": {}, "B": {}}, run_now=True)

        assert execute == [("A", False), ("B", False), ("B", True)]
        assert result["success"] == 2
        assert result["retry"]["success"] == 1

    def test_retry_only_skips_new_downloads(self, execute):
        jobStateRepository.save_state("watchlist", "B", {"missing": [{"record_id": "1"}]})

        result = job.run_watchlist_job(configs={"A": {}, "B": {}}, run_now=True, retry_only=True)

        assert execute == [("B", True)]
        assert result["total"] == 0

    def test_still_held_lists_are_exported_to_csv(self, monkeypatch, execute):
        jobStateRepository.save_state("watchlist", "A", {
            "missing": [{"record_id": "1", "detail_url": "https://x/1"}],
        })
        monkeypatch.setattr(job, "_execute_watchlist",
                            lambda name, retry_held=False, log_paths=None: SourceOutcome(status="WAITING"))

        result = job.run_watchlist_job(configs={"A": {}}, run_now=True)

        assert result["still_pending"] == 1
        assert os.path.isfile(result["pending_csv_path"])

    def test_cli_rejects_unknown_list(self):
        with pytest.raises(SystemExit):
            job.main(["--source", "NOT_A_REAL_LIST"])

    def test_cli_source_runs_now(self, monkeypatch):
        name = next(iter(job.WATCHLIST_CONFIGS))
        run = MagicMock(return_value={"failed": 0, "retry": {"failed": 0}})
        monkeypatch.setattr(job, "run_watchlist_job", run)

        assert job.main(["--source", name]) == 0
        assert run.call_args.kwargs == {
            "configs": {name: job.WATCHLIST_CONFIGS[name]},
            "run_now": True,
            "retry_only": False,
        }
