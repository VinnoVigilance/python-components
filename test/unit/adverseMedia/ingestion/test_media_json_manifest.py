"""Manifest-based media discovery uses the same selection rules as listings."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from scrapy.http import TextResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from services.adverseMediaPipeline.mediaDiscoveryService import MediaDiscoveryService


def json_response(url, payload):
    return TextResponse(url=url, body=json.dumps(payload).encode(), encoding="utf-8")


def spider_with(service):
    return MediaSpider(
        task=SimpleNamespace(url="https://example.test/manifest.json", detail_items=None),
        source_config={"discovery": {"strategy": "json_manifest", "policy": "stop_after_known"}},
        storage=None, records=[], discovery_service=service,
    )


def test_manifest_discovers_ordered_articles_across_files():
    service = MagicMock()
    service.build_record_key.side_effect = lambda record: record["SourceURL"]
    service.check_record_key.return_value = (False, False)
    spider = spider_with(service)
    manifest = json_response("https://example.test/manifest.json", {
        "shards": [{"path": "/one.json", "count": 1}, {"path": "/two.json", "count": 1}]
    })
    first = list(spider.parse_json_manifest(manifest))[0]
    assert first.url == "https://example.test/one.json"
    outputs = list(spider.parse_json_shard(
        json_response(first.url, {"items": [{"date": "2026-06-02", "url": "/new"}]}),
        **first.cb_kwargs,
    ))
    assert [request.url for request in outputs] == ["https://example.test/new", "https://example.test/two.json"]
    second = outputs[1]
    results = list(spider.parse_json_shard(
        json_response(second.url, {"items": [{"date": "2026-06-01", "url": "/older"}]}),
        **second.cb_kwargs,
    ))
    assert [request.url for request in results] == ["https://example.test/older"]
    service.mark_source_end.assert_called_once()


def test_bad_entry_disables_early_stop_and_marks_run_incomplete():
    service = MediaDiscoveryService(
        cursor=MagicMock(), source_id=1, dataset_id=2, source_config={},
        stop_after_known=True, known_threshold=1,
    )
    spider = spider_with(service)
    shard = json_response("https://example.test/one.json", {
        "items": [{"date": "invalid", "url": "/broken"}]
    })
    assert list(spider.parse_json_shard(shard, [shard.url], [1], 0, None)) == []
    assert service.discovery_failure_count == 1
    assert service.stop_after_known is False


def test_manifest_rejects_file_from_another_host():
    service = MagicMock()
    spider = spider_with(service)
    manifest = json_response("https://example.test/manifest.json", {
        "shards": [{"path": "https://other.test/one.json", "count": 1}]
    })
    assert list(spider.parse_json_manifest(manifest)) == []
    service.mark_discovery_failure.assert_called_once_with("INVALID_JSON_MANIFEST")
