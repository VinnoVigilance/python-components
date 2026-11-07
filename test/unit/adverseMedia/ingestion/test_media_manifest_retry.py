"""Failed manifest entries remain identifiable through retries."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from scrapy.http import TextResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from services.adverseMediaPipeline import mediaTodoService


def _spider():
    return MediaSpider(
        task=SimpleNamespace(url="https://example.test/manifest.json"),
        source_config={"discovery": {"strategy": "json_manifest"},
                       "identity": {"record_key": {"fields": ["SourceURL"]}}},
        storage=None, records=[], discovery_service=MagicMock(),
    )


def _response(items):
    return TextResponse(url="https://example.test/one.json",
                        body=json.dumps({"items": items}).encode(), encoding="utf-8")


def test_failed_entry_is_saved_retried_by_url_and_removed_after_success():
    spider = _spider()
    broken = {"date": "invalid", "url": "/article"}
    response = _response([broken])
    assert list(spider.parse_json_shard(response, [response.url], [1], 0, None)) == []
    retry_item = spider.discovery_service.mark_discovery_failure.call_args.kwargs["retry_item"]
    counts = mediaTodoService.update_items("EXAMPLE", [], [], [retry_item])
    assert counts["todo_pending_count"] == 1
    [saved] = mediaTodoService.load_items("EXAMPLE")

    # A new entry has moved the original from position 1 to position 2.
    fixed = _response([{"date": "2026-06-02", "url": "/new"},
                       {"date": "2026-06-01", "url": "/article"}])
    [request] = list(spider.retry_json_entry(fixed, saved))
    assert request.url == "https://example.test/article"
    assert request.cb_kwargs["discovery_retry_key"] == saved["record_key"]
    result = mediaTodoService.update_items(
        "EXAMPLE", [{"record_key": "article", "discovery_retry_key": saved["record_key"]}],
        [{"status": "INSERTED"}],
    )
    assert result["todo_removed_count"] == 1
    assert mediaTodoService.load_items("EXAMPLE") == []


def test_ambiguous_entry_stays_failed_for_manual_review():
    spider = _spider()
    saved = {"record_key": "retry-key", "index_file_url": "https://example.test/one.json",
             "entry_url": "/article"}
    duplicate = {"date": "2026-06-01", "url": "/article"}
    [failure] = list(spider.retry_json_entry(_response([duplicate, duplicate]), saved))
    assert failure["failed"] is True
    assert failure["discovery_retry_item"] == saved
    assert "ambiguous" in failure["error"]
