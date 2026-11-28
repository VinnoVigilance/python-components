import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

import pytest
from scrapy.http import TextResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from services.adverseMediaPipeline.mediaDiscoveryService import MediaDiscoveryService


pytestmark = pytest.mark.unit
BASE = "https://news.example.test"
FEED = BASE + "/news?format=feed&type=rss"


def make_spider(monkeypatch, known=(), *, incremental=False):
    config = {
        "source_name": "EXAMPLE", "dataset_name": "EXAMPLE_NEWS",
        "discovery": {
            "strategy": "rss", "policy": "stop_after_known" if incremental else "full_scan",
            "feed_urls": [FEED],
            "pagination": {"type": "offset", "page_size": 20, "offset_param": "start", "size_param": None},
            "use_item_pub_date": True,
        },
        "extraction": {
            "SourceRecordId": {
                "source": "response_url",
                "extraction": {"strategy": "regex", "pattern": r"/news/([^/?#]+)$"},
            },
        },
        "identity": {"record_key": {"fields": ["source_name", "dataset_name", "SourceRecordId"]}},
    }
    monkeypatch.setattr(
        "services.adverseMediaPipeline.mediaDiscoveryService.exists_by_record_key",
        lambda **kwargs: kwargs["record_key"].rsplit("|", 1)[-1] in known,
    )
    service = MediaDiscoveryService(None, 1, 2, config, incremental, known_threshold=2)
    spider = MediaSpider(SimpleNamespace(url=BASE + "/news"), config, None, [], service)

    async def first_request():
        return [request async for request in spider.start()][0]

    return spider, service, asyncio.run(first_request())


def parse(request, names):
    entries = []
    for index, name in enumerate(names):
        published = format_datetime(
            datetime(2026, 11, 20, tzinfo=timezone.utc)
            - timedelta(days=request.cb_kwargs.get("offset", 0) + index)
        )
        entries.append(
            f"<item><link>{BASE}/news/{escape(name)}</link>"
            f"<pubDate>{published}</pubDate></item>"
        )
    response = TextResponse(request.url, body=("<rss><channel>" + "".join(entries) + "</channel></rss>").encode())
    return list(request.callback(response, **request.cb_kwargs))


def test_initial_reads_every_rss_page_and_uses_actual_item_count(monkeypatch):
    _, service, first = make_spider(monkeypatch, {"old"})
    old_detail, new_detail, second = parse(first, ["old", "new"])
    assert old_detail.url == BASE + "/news/old"
    assert new_detail.cb_kwargs["identity_fields"]["date.originalValue"] == "2026-11-19"
    assert parse_qs(urlsplit(second.url).query)["start"] == ["2"]
    older_detail, third = parse(second, ["older"])
    assert older_detail.url == BASE + "/news/older"
    assert parse_qs(urlsplit(third.url).query)["start"] == ["3"]
    assert parse(third, []) == []
    assert service.get_summary()["completed_safely"]
    assert service.reached_source_end


def test_incremental_stops_after_consecutive_known_on_later_page(monkeypatch):
    _, service, first = make_spider(monkeypatch, {"old1", "old2"}, incremental=True)
    new_detail, second = parse(first, ["new", "old1"])
    assert new_detail.url == BASE + "/news/new"
    assert parse_qs(urlsplit(second.url).query)["start"] == ["2"]
    assert parse(second, ["old2", "older"]) == []
    summary = service.get_summary()
    assert summary["stop_reason"] == "KNOWN_THRESHOLD_REACHED"
    assert summary["completed_safely"]
    assert not service.reached_source_end
    assert summary["discovered_count"] == 3


def test_repeated_rss_page_is_incomplete(monkeypatch):
    _, service, first = make_spider(monkeypatch)
    _, second = parse(first, ["one"])
    assert parse(second, ["one"]) == []
    assert service.discovery_failure_count == 1
    assert not service.get_summary()["completed_safely"]


def test_empty_first_rss_page_is_an_error(monkeypatch):
    _, service, first = make_spider(monkeypatch)
    assert parse(first, []) == []
    assert not service.get_summary()["completed_safely"]


def test_out_of_scope_publisher_feed_item_is_skipped_safely(monkeypatch):
    spider, service, first = make_spider(monkeypatch)
    spider.discovery_config["article_url_pattern"] = r"/news/"
    published = format_datetime(datetime(2026, 11, 20, tzinfo=timezone.utc))
    body = (
        f"<rss><channel><item><link>{BASE}/other/2026/11/20/123/story</link>"
        f"<pubDate>{published}</pubDate></item>"
        f"<item><link>{BASE}/news/valid</link><pubDate>{published}</pubDate></item>"
        "</channel></rss>"
    ).encode()
    response = TextResponse(first.url, body=body)
    detail, next_page = list(first.callback(response, **first.cb_kwargs))
    assert detail.url == BASE + "/news/valid"
    assert parse(next_page, []) == []
    assert service.discovery_failure_count == 0
    assert service.get_summary()["completed_safely"]
