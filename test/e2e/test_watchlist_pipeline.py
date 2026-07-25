"""
End-to-end wiring test for pipelines/watchlistPipeline.run_watchlist_pipeline.

This proves the whole chain is wired together correctly -- download -> hash ->
duplicate check -> version/store -> raw -> attachments -> core -> result -- WITHOUT
touching the network, storage, or a real database. Every I/O boundary (the four
service modules) is replaced with a mock, so what we are testing is the
orchestration itself: that each step is called, in order, with the values the
previous step produced, and that the final result dict is assembled correctly.

Because everything is mocked it is safe and fast, but it imports the full
pipeline stack, so it is marked `e2e` and kept out of the lean CI unit job.
Run it locally with:  pytest -m e2e
"""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# The pipeline imports the storage client, which needs boto3. If the runtime
# stack is not fully installed, skip this module cleanly rather than erroring
# the whole test run.
pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")

# Importing the pipeline pulls in config/settings.py, which requires these at
# import time. Provide harmless dummies so the import succeeds; the tests mock
# every real storage/database call, so these values are never actually used.
# setdefault means a real .env value (if present) still wins.
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

import pipelines.watchlistPipeline as wp  # noqa: E402

pytestmark = pytest.mark.e2e


def _mock_services(monkeypatch, duplicate_status, extra_dup=None):
    """Replace every service call the orchestrator makes with a mock.

    Returns the dict of mocks so a test can assert how they were called.
    """
    fs = wp.watchlistFileService
    raw = wp.watchlistRawService
    att = wp.watchlistAttachmentService
    core = wp.watchlistCoreService

    dup_result = {"duplicate_status": duplicate_status}
    if extra_dup:
        dup_result.update(extra_dup)

    mocks = {
        "acquire_source_file": MagicMock(return_value=Path("source.xml")),
        "calculate_file_metadata": MagicMock(return_value={
            "file_hash": "abc123", "file_name": "source.xml", "file_size": 42,
        }),
        "resolve_lookup_values": MagicMock(return_value={
            "source_id": 1, "list_type_id": 2,
        }),
        "check_duplicate": MagicMock(return_value=dup_result),
        "determine_file_version": MagicMock(return_value=7),
        "store_source_file": MagicMock(return_value="s3://ingestion/source.xml"),
        "insert_watchlist_file": MagicMock(return_value=123),
        "insert_file_log": MagicMock(return_value=None),
        "process_raw": MagicMock(return_value={
            "parsed_record_count": 10,
            "processed_record_count": 9,
            "raw_record_count": 9,
        }),
        "process_attachments": MagicMock(return_value={
            "processed_count": 2, "new_count": 1, "reused_count": 1,
            "member_mapping_count": 2, "list_mapping_count": 0,
        }),
        "process_core": MagicMock(return_value={
            "processed_count": 9, "new_count": 5, "updated_count": 3,
            "skipped_count": 1, "deleted_count": 0,
        }),
    }

    monkeypatch.setattr(fs, "acquire_source_file", mocks["acquire_source_file"])
    monkeypatch.setattr(fs, "calculate_file_metadata", mocks["calculate_file_metadata"])
    monkeypatch.setattr(fs, "resolve_lookup_values", mocks["resolve_lookup_values"])
    monkeypatch.setattr(fs, "check_duplicate", mocks["check_duplicate"])
    monkeypatch.setattr(fs, "determine_file_version", mocks["determine_file_version"])
    monkeypatch.setattr(fs, "store_source_file", mocks["store_source_file"])
    monkeypatch.setattr(fs, "insert_watchlist_file", mocks["insert_watchlist_file"])
    monkeypatch.setattr(fs, "insert_file_log", mocks["insert_file_log"])
    monkeypatch.setattr(raw, "process_watchlist_file", mocks["process_raw"])
    monkeypatch.setattr(att, "process_attachments", mocks["process_attachments"])
    monkeypatch.setattr(core, "process_watchlist_file", mocks["process_core"])

    return mocks


def test_first_download_runs_the_full_chain(monkeypatch):
    mocks = _mock_services(monkeypatch, duplicate_status="FIRST_DOWNLOAD")

    result = wp.run_watchlist_pipeline("OFAC-SDN")

    # --- the orchestration produced a fully-normalized result ---
    assert result["pipeline_result"] == "NORMALIZED"
    assert result["duplicate_status"] == "FIRST_DOWNLOAD"
    assert result["watchlist_file_id"] == 123
    assert result["file_version"] == 7
    assert result["storage_path"] == "s3://ingestion/source.xml"

    # --- counts flowed through from each stage ---
    assert result["parsed_record_count"] == 10
    assert result["raw_record_count"] == 9
    assert result["attachment_new_count"] == 1
    assert result["core_new_count"] == 5

    # --- config values were carried onto the result ---
    assert result["source_name"] == "OFAC"        # from WATCHLIST_CONFIGS["OFAC-SDN"]
    assert result["list_name"] == "OFAC-SDN"

    # --- the chain actually ran, and later steps got the id from earlier ones ---
    mocks["acquire_source_file"].assert_called_once()
    mocks["process_raw"].assert_called_once()
    assert mocks["process_raw"].call_args.kwargs["watchlist_file_id"] == 123
    mocks["process_core"].assert_called_once()
    assert mocks["process_core"].call_args.kwargs["watchlist_file_id"] == 123


def test_exact_duplicate_short_circuits_before_processing(monkeypatch):
    mocks = _mock_services(
        monkeypatch,
        duplicate_status="DUPLICATE_COMPLETED",
        extra_dup={
            "watchlist_file_id": 50,
            "file_version": 3,
            "storage_path": "s3://ingestion/old.xml",
        },
    )

    result = wp.run_watchlist_pipeline("OFAC-SDN")

    # It skipped, and reused the existing file's identity ...
    assert result["pipeline_result"] == "SKIPPED"
    assert result["watchlist_file_id"] == 50
    assert result["file_version"] == 3
    assert result["raw_record_count"] == 0

    # ... and crucially did NOT re-run the expensive processing stages.
    mocks["process_raw"].assert_not_called()
    mocks["process_attachments"].assert_not_called()
    mocks["process_core"].assert_not_called()
    # A SKIPPED download was logged.
    mocks["insert_file_log"].assert_called_once()


def test_unknown_watchlist_raises():
    with pytest.raises(ValueError, match="Unknown watchlist"):
        wp.run_watchlist_pipeline("NOT_A_REAL_LIST")
