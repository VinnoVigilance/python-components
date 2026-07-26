"""
Unit tests for services/watchlistPipeline/watchlistRawService.py

Covers the Raw Layer rules from the Data Insertion Guideline
("raw.unparsed_watchlist_payload"):

  * One Record = One Source Entity (section 5): each parsed entity becomes one
    payload row -- the service must never collapse many entities into one.
  * External ID Rules (section 8): external_id must come from the source; a
    record missing it is rejected rather than silently inserted.
  * Raw insertion is logged as a RAW_INSERT SUCCESS event (file_log section).

insert_raw_payloads() is tested directly with the DB mocked, because the
row-building + external_id enforcement is the pure logic worth locking down.
"""

import os
from unittest.mock import MagicMock

import pytest

pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

from services.watchlistPipeline import watchlistRawService as raw  # noqa: E402

pytestmark = pytest.mark.unit


def _patch_db(monkeypatch, inserted_count):
    monkeypatch.setattr(raw, "connection_pool", MagicMock())
    monkeypatch.setattr(
        raw.rawPayloadRepository,
        "insert_raw_payloads",
        MagicMock(return_value=inserted_count),
    )
    monkeypatch.setattr(raw.watchlistFileRepository, "mark_file_as_parsed", MagicMock())
    monkeypatch.setattr(raw.watchlistFileLogRepository, "insert_file_log", MagicMock())


def test_each_record_becomes_one_payload_row(monkeypatch):
    _patch_db(monkeypatch, inserted_count=3)

    records = [
        {"uid": "A001", "name": "One"},
        {"uid": "A002", "name": "Two"},
        {"uid": "A003", "name": "Three"},
    ]

    count = raw.insert_raw_payloads(
        watchlist_file_id=100,
        records=records,
        external_id_path="uid",
    )

    assert count == 3
    # The repository was handed exactly one (external_id, record) pair per entity.
    payloads = raw.rawPayloadRepository.insert_raw_payloads.call_args.kwargs["payloads"]
    assert [external_id for external_id, _ in payloads] == ["A001", "A002", "A003"]
    assert [rec for _, rec in payloads] == records


def test_record_missing_external_id_is_rejected(monkeypatch):
    _patch_db(monkeypatch, inserted_count=0)

    records = [
        {"uid": "A001", "name": "One"},
        {"uid": "", "name": "MissingId"},  # blank external id
    ]

    # The guideline forbids generating our own id -- a source entity without one
    # must fail, naming the offending record, not be inserted silently.
    with pytest.raises(ValueError, match="record number 2"):
        raw.insert_raw_payloads(
            watchlist_file_id=100,
            records=records,
            external_id_path="uid",
        )

    raw.rawPayloadRepository.insert_raw_payloads.assert_not_called()


def test_raw_insert_success_is_logged(monkeypatch):
    _patch_db(monkeypatch, inserted_count=1)

    raw.insert_raw_payloads(
        watchlist_file_id=100,
        records=[{"uid": "A001"}],
        external_id_path="uid",
    )

    logged = raw.watchlistFileLogRepository.insert_file_log.call_args.kwargs
    assert logged["step"] == "RAW_INSERT"
    assert logged["status"] == "SUCCESS"
    # The file is also marked PARSED after its raw rows land.
    raw.watchlistFileRepository.mark_file_as_parsed.assert_called_once()
