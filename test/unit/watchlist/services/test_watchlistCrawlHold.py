"""Unit tests for the watchlist crawl hold in services/watchlistPipeline/watchlistFileService.py."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

from repositories import jobStateRepository  # noqa: E402
from services.watchlistPipeline import watchlistFileService as fs  # noqa: E402

pytestmark = pytest.mark.unit

CONFIG = {
    "list_name": "NCA-MOST-WANTED",
    "source_name": "NCA",
    "url": "https://nca/list",
    "source_config": "config/watchlistSources/nca.yaml",
}


def _missing(record_id, permanent=False):
    return {
        "record_id": record_id,
        "detail_url": f"https://nca/{record_id}",
        "list_data": {"name": record_id},
        "error": "HTTP 404" if permanent else "timeout",
        "permanent": permanent,
    }


def _list_only(item):
    return {"source_record_id": item["record_id"], "list": item["list_data"], "detail": {}}


def _crawl_result(records, missing, expected, broken=()):
    return SimpleNamespace(
        records=records,
        missing_details=missing,
        missing_detail_count=len(missing),
        selected_detail_count=expected,
        broken_details=list(broken),
    )


def _held():
    return jobStateRepository.load_state(fs.WATCHLIST_HOLD_KIND, CONFIG["list_name"])


class TestHoldIncompleteCrawl:
    def test_complete_crawl_is_not_held_and_clears_old_hold(self, tmp_path):
        jobStateRepository.save_state(fs.WATCHLIST_HOLD_KIND, CONFIG["list_name"], {"old": True})

        broken = fs.hold_incomplete_crawl(CONFIG, _crawl_result([{}], [], 1), tmp_path / "l.html")

        assert broken == []
        assert _held() is None

    def test_pages_broken_on_source_do_not_hold_the_list(self, tmp_path):
        gone = _missing("9", permanent=True)

        result = _crawl_result([{}, _list_only(gone)], [], 2, broken=[gone])

        broken = fs.hold_incomplete_crawl(CONFIG, result, tmp_path / "l.html")

        assert broken == [gone]
        assert _held() is None

    def test_temporary_failure_holds_the_list(self, tmp_path):
        gone = _missing("3", permanent=True)
        records = [{"source_record_id": "1"}, _list_only(gone)]
        missing = [_missing("2")]
        result = _crawl_result(records, missing, 3, broken=[gone])

        with pytest.raises(fs.CrawlOnHold) as hold:
            fs.hold_incomplete_crawl(CONFIG, result, tmp_path / "l.html")

        assert hold.value.expected_count == 3
        assert hold.value.missing_urls == ["https://nca/2"]
        assert "read 2 of 3 detail pages" in str(hold.value)
        held = _held()
        assert held["records"] == records
        assert held["missing"] == missing
        assert held["broken"] == [gone]
        assert held["attempts"] == 1
        assert held["source_file_path"] == str(tmp_path / "l.html")


class TestRetryHeldCrawl:
    def _hold(self, tmp_path):
        jobStateRepository.save_state(fs.WATCHLIST_HOLD_KIND, CONFIG["list_name"], {
            "source_file_path": str(tmp_path / "l.html"),
            "attempts": 1,
            "expected_count": 3,
            "records": [{"source_record_id": "1"}],
            "missing": [_missing("2"), _missing("3")],
        })

    def test_nothing_held_returns_none(self, monkeypatch):
        crawl = MagicMock()
        monkeypatch.setattr(fs, "crawl", crawl)

        assert fs.retry_held_crawl(CONFIG) is None
        crawl.assert_not_called()

    def test_retry_fetches_only_missing_pages_and_returns_full_list(self, monkeypatch, tmp_path):
        self._hold(tmp_path)
        new_records = [{"source_record_id": "2"}, {"source_record_id": "3"}]
        crawl = MagicMock(return_value=_crawl_result(new_records, [], 2))
        monkeypatch.setattr(fs, "crawl", crawl)

        result = fs.retry_held_crawl(CONFIG)

        task = crawl.call_args.args[0]
        assert [item["record_id"] for item in task.detail_items] == ["2", "3"]
        assert task.source_file_path == str(tmp_path / "l.html")
        assert [r["source_record_id"] for r in result.records] == ["1", "2", "3"]
        assert result.broken_details == []

    def test_retry_that_only_finds_broken_pages_releases_the_list(self, monkeypatch, tmp_path):
        self._hold(tmp_path)
        gone = _missing("3", permanent=True)
        monkeypatch.setattr(fs, "crawl", MagicMock(
            return_value=_crawl_result([{"source_record_id": "2"}, _list_only(gone)], [], 2, broken=[gone])
        ))

        result = fs.retry_held_crawl(CONFIG)

        assert [r["source_record_id"] for r in result.records] == ["1", "2", "3"]
        assert result.broken_details == [gone]

    def test_retry_still_missing_stays_held(self, monkeypatch, tmp_path):
        self._hold(tmp_path)
        monkeypatch.setattr(fs, "crawl", MagicMock(
            return_value=_crawl_result([{"source_record_id": "2"}], [_missing("3")], 2)
        ))

        with pytest.raises(fs.CrawlOnHold):
            fs.retry_held_crawl(CONFIG)

        held = _held()
        assert held["attempts"] == 2
        assert len(held["records"]) == 2
        assert [item["record_id"] for item in held["missing"]] == ["3"]

    def test_release_deletes_the_hold(self, tmp_path):
        self._hold(tmp_path)

        fs.release_held_crawl(CONFIG)

        assert _held() is None
