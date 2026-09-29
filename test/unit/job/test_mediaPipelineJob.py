"""Unit tests for jobs/mediaPipelineJob.py (to-do retry passes, CLI flags)."""

import os
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

import config.loggingConfig as loggingConfig  # noqa: E402
import jobs.mediaPipelineJob as job  # noqa: E402
import pipelines.mediaPipeline as mp  # noqa: E402
from repositories import jobStateRepository  # noqa: E402
from services.job.jobRunner import SourceOutcome  # noqa: E402

pytestmark = pytest.mark.unit

MEDIA_CONFIG = {"sources": {"ON": {"enabled": True}, "ON2": {}, "OFF": {"enabled": False}}}


@pytest.fixture(autouse=True)
def _fake_config(monkeypatch):
    monkeypatch.setattr(job, "load_media_config", lambda: MEDIA_CONFIG)


class TestWorker:
    def test_run_status_and_counts_are_mapped(self, monkeypatch):
        monkeypatch.setattr(loggingConfig, "configure_logging", lambda: None)
        monkeypatch.setattr(mp, "run_media_pipeline", MagicMock(return_value={
            "run_status": "PARTIAL",
            "discovered_count": 5,
            "core_inserted_count": 3,
            "total_failed_count": 2,
            "todo_pending_count": 2,
            "stop_reason": "KNOWN_THRESHOLD",
        }))

        outcome = job._run_media_worker("ON", "INCREMENTAL")

        assert outcome.status == "PARTIAL"
        assert outcome.counts["core_new_count"] == 3
        assert outcome.counts["failed_item_count"] == 2
        assert outcome.counts["todo_pending_count"] == 2

    def test_failed_run_raises_so_the_runner_retries(self, monkeypatch):
        monkeypatch.setattr(loggingConfig, "configure_logging", lambda: None)
        monkeypatch.setattr(mp, "run_media_pipeline", MagicMock(return_value={
            "run_status": "FAILED", "failure_summary": {"DETAIL_FETCH": 1},
        }))

        with pytest.raises(RuntimeError, match="Media run failed"):
            job._run_media_worker("ON", "INCREMENTAL")


class TestJob:
    @pytest.fixture
    def execute(self, monkeypatch, tmp_path):
        calls = []

        def fake_execute(name, mode, log_paths=None):
            calls.append((name, mode))
            if mode == "RETRY":
                jobStateRepository.delete_state("media", name)
            return SourceOutcome(status="SUCCESS")

        monkeypatch.setattr(job, "_execute_media", fake_execute)
        monkeypatch.setattr(job, "advisory_job_lock", lambda lock_id: nullcontext(True))
        monkeypatch.setattr(job, "PROJECT_ROOT", tmp_path)
        return calls

    def test_only_enabled_sources_run(self, execute):
        job.run_media_job()

        assert execute == [("ON", "INCREMENTAL"), ("ON2", "INCREMENTAL")]

    def test_todo_is_retried_before_the_main_run(self, execute):
        jobStateRepository.save_state("media", "ON2", {"items": {"k": {"record_key": "k"}}})

        result = job.run_media_job(mode="INITIAL", dataset_names=["ON2"])

        assert execute == [("ON2", "RETRY"), ("ON2", "INITIAL")]
        assert result["retry"]["success"] == 1

    def test_retry_only_skips_discovery(self, execute):
        jobStateRepository.save_state("media", "ON", {"items": {"k": {"record_key": "k"}}})

        result = job.run_media_job(retry_only=True)

        assert execute == [("ON", "RETRY")]
        assert result["total"] == 0


class TestCli:
    def test_disabled_source_is_rejected(self):
        with pytest.raises(SystemExit) as exit_info:
            job.main(["--source", "OFF"])

        assert exit_info.value.code == 2

    def test_unknown_source_is_rejected(self):
        with pytest.raises(SystemExit):
            job.main(["--source", "NOPE"])

    def test_enabled_source_runs(self, monkeypatch):
        run = MagicMock(return_value={"failed": 0, "retry": {"failed": 0}})
        monkeypatch.setattr(job, "run_media_job", run)

        assert job.main(["--source", "ON", "--mode", "initial"]) == 0
        assert run.call_args.kwargs == {
            "mode": "INITIAL", "dataset_names": ["ON"], "retry_only": False,
        }

    def test_failed_retry_gives_exit_code_1(self, monkeypatch):
        monkeypatch.setattr(job, "run_media_job",
                            MagicMock(return_value={"failed": 0, "retry": {"failed": 1}}))

        assert job.main([]) == 1
