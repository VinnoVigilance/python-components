import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from scrapy.http import HtmlResponse, TextResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from services.adverseMediaPipeline.mediaDiscoveryService import MediaDiscoveryService

pytestmark = pytest.mark.unit
BASE = "https://news.example.test/"


def make_spider(monkeypatch, known, *, incremental):
    config = {
        "source_name": "EXAMPLE", "dataset_name": "EXAMPLE_NEWS",
        "identity": {"record_key": {"fields": ["source_name", "dataset_name", "SourceRecordId"]}},
        "discovery": {
            "strategy": "listing_groups", "policy": "full_scan", "skip_known_details": True,
            "start_url": "https://api.example.test/articles", "article_base_url": BASE,
            "items_path": "items", "url_path": "url", "sort_date_path": "published",
            "pagination": {"type": "offset", "page_size": 12, "size_param": "limit"},
            "groups": [
                {"params": {"category": "first"}}, {"params": {"category": "second"}},
                {"detail_urls": [BASE + "story/series"]},
            ],
        },
        "extraction": {
            "SourceRecordId": {
                "source": "response_url",
                "extraction": {"strategy": "regex", "pattern": r"/story/([^/?#]+)$"},
            },
            "Title": {"selector": "h1", "required": True},
            "BodyText": {"selector": "article p", "join_with": "\n\n", "required": True},
        },
        "extraction_by_url": [
            {"pattern": "/series$", "fields": {"Title": {"selector": "title"}}},
        ],
    }
    monkeypatch.setattr(
        "services.adverseMediaPipeline.mediaDiscoveryService.exists_by_record_key",
        lambda **kwargs: kwargs["record_key"].rsplit("|", 1)[-1] in known,
    )
    discovery = MediaDiscoveryService(None, 1, 2, config, incremental, known_threshold=2)
    spider = MediaSpider(SimpleNamespace(url=BASE), config, None, [], discovery)

    async def first_request():
        return [request async for request in spider.start()][0]

    return spider, discovery, asyncio.run(first_request())


def item(name, day=20):
    return {"url": f"/story/{name}", "published": f"2026-11-{day:02}T10:00:00Z"}


def parse(request, items):
    response = TextResponse(request.url, body=json.dumps({"items": items}).encode())
    return list(request.callback(response, **request.cb_kwargs))


def test_initial_scans_past_known_and_uses_actual_offset(monkeypatch):
    _, discovery, first = make_spider(monkeypatch, {"a", "b"}, incremental=False)
    [second] = parse(first, [item("a"), item("b")])
    assert parse_qs(urlsplit(second.url).query)["offset"] == ["2"]
    detail, last = parse(second, [item("c", day=19)])
    assert detail.url == BASE + "story/c"
    [next_group] = parse(last, [])
    assert next_group.cb_kwargs == {"index": 1, "offset": 0}
    assert not discovery.reached_source_end


def test_incremental_stops_each_group_without_counting_duplicates(monkeypatch):
    _, discovery, first = make_spider(monkeypatch, {"a", "b", "c", "d", "e"}, incremental=True)
    new_detail, next_group = parse(first, [item("a"), item("new"), item("b"), item("b"), item("c")])
    assert new_detail.url == BASE + "story/new"
    assert next_group.cb_kwargs == {"index": 1, "offset": 0}
    assert discovery.consecutive_known_records == 0
    [next_page] = parse(next_group, [item("a"), item("d")])
    assert next_page.cb_kwargs == {"index": 1, "offset": 2}
    [legacy_detail] = parse(next_page, [item("e")])
    assert legacy_detail.url == BASE + "story/series"
    summary = discovery.get_summary()
    assert summary["known_count"] == 5
    assert summary["completed_safely"]
    assert summary["stop_reason"] == "KNOWN_THRESHOLD_REACHED"
    assert not summary["reached_source_end"]


def test_unordered_page_scans_past_known(monkeypatch):
    _, discovery, first = make_spider(monkeypatch, {"a", "b"}, incremental=True)
    detail, next_page = parse(first, [item("a", 19), item("b"), item("new")])
    assert detail.url == BASE + "story/new"
    assert next_page.cb_kwargs["index"] == 0
    assert not discovery.stop_after_known


@pytest.mark.parametrize("bad_items", [None, [item("a")]])
def test_invalid_or_repeated_page_is_incomplete(monkeypatch, bad_items):
    _, discovery, first = make_spider(monkeypatch, {"a"}, incremental=False)
    [second] = parse(first, [item("a")])
    [next_group] = parse(second, bad_items)
    _, last = parse(next_group, [item("new")])
    parse(last, [])
    assert not discovery.get_summary()["completed_safely"]
    assert discovery.discovery_failure_count == 1


def test_invalid_url_preserves_other_articles_and_next_page(monkeypatch):
    _, discovery, first = make_spider(monkeypatch, {"known1", "known2"}, incremental=True)
    new_request, next_page = parse(first, [
        item("known1"), item("known2"), {"url": "https://other.test/story/a"}, item("new"),
    ])
    assert new_request.url == BASE + "story/new"
    assert next_page.cb_kwargs == {"index": 0, "offset": 4}
    assert discovery.discovery_failure_count == 1
    assert not discovery.stop_after_known
    older_request, last = parse(next_page, [item("older", 19)])
    assert older_request.url == BASE + "story/older"
    assert parse(last, [])[0].cb_kwargs["index"] == 1


def test_wholly_invalid_page_advances_but_repeating_it_does_not(monkeypatch):
    _, discovery, first = make_spider(monkeypatch, set(), incremental=False)
    invalid_items = [{"url": "/not-an-article"}]
    [next_page] = parse(first, invalid_items)
    assert next_page.cb_kwargs == {"index": 0, "offset": 1}
    [next_group] = parse(next_page, invalid_items)
    assert next_group.cb_kwargs == {"index": 1, "offset": 0}
    assert discovery.discovery_failure_count == 2


def test_unusable_listing_date_does_not_drop_valid_url(monkeypatch):
    _, discovery, first = make_spider(monkeypatch, {"known1", "known2"}, incremental=True)
    new_request, next_page = parse(first, [
        item("known1"), item("known2"), {"url": "/story/new", "published": "invalid"},
    ])
    assert new_request.url == BASE + "story/new"
    assert next_page.cb_kwargs == {"index": 0, "offset": 3}
    assert discovery.discovery_failure_count == 0
    assert not discovery.stop_after_known


def test_html_listing_group_discovers_links_and_uses_landing_page(monkeypatch):
    spider, discovery, _ = make_spider(monkeypatch, set(), incremental=False)
    spider.discovery_config["groups"][2] = {
        "url": BASE + "story/series",
        "link_selector": "a[href^='/story/part']",
        "include_page": True,
    }
    spider.storage = SimpleNamespace(save_detail_html=lambda **kwargs: "saved.html")
    [landing] = list(spider._start_listing_group(2))
    response = HtmlResponse(
        landing.url,
        body=(b"<title>Series title</title><article><p>Introduction.</p></article>"
              b"<a href='/story/part1'>One</a><a href='/story/part2'>Two</a>"
              b"<a href='/story/part1'>Duplicate</a>"),
        encoding="utf-8",
    )
    results = list(landing.callback(response, **landing.cb_kwargs))
    assert results[0]["extracted"]["BodyText"] == "Introduction."
    assert [result.url for result in results[1:]] == [
        BASE + "story/part1", BASE + "story/part2",
    ]
    assert discovery.reached_source_end
    assert discovery.discovered_count == 3


def test_empty_html_listing_group_is_not_a_complete_archive(monkeypatch):
    spider, discovery, _ = make_spider(monkeypatch, set(), incremental=False)
    spider.discovery_config["groups"][2] = {
        "url": BASE + "story/series", "link_selector": "a[href^='/story/part']",
        "include_page": True,
    }
    [landing] = list(spider._start_listing_group(2))
    response = HtmlResponse(landing.url, body=b"<title>No links</title>")
    assert list(landing.callback(response, **landing.cb_kwargs)) == []
    assert discovery.discovery_failure_count == 1
    assert not discovery.get_summary()["completed_safely"]


@pytest.mark.parametrize("path", [
    "/news/nation/2026/11/23/example-1234",
    "/news/01/31/24/example",
    "/business/12/20/23/example",
    "/video/business/12/20/23/example",
    "/halalan2016/business/06/03/16/example",
    "/sports/othersports/2026/3/25/example-1949",
    "/2026/3/25/example-1011",
    "/war-on-drugs/part1",
    "/war-on-drugs/part7",
])
def test_abs_cbn_archive_urls_keep_path_as_record_identity(path):
    config_path = Path(__file__).resolve().parents[4] / "config/mediaSources.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))["sources"]["ABS_CBN_NEWS"]
    spider = MediaSpider(None, config, None, [], None)
    url = f"https://www.abs-cbn.com{path}"
    assert spider._listing_group_url(url + "?tracking=1#article") == url
    assert spider._extract_source_record_id(url) == path.lstrip("/")


def test_url_variant_reuses_field_extraction_without_losing_paragraphs(monkeypatch):
    spider, _, _ = make_spider(monkeypatch, set(), incremental=False)
    response = HtmlResponse(
        BASE + "story/series",
        body=b"<title>Series title</title><h1>Logo</h1><article><p>One.</p><p>Two.</p></article>",
    )
    record = spider._extract_record(response)
    assert record["Title"] == "Series title"
    assert record["BodyText"] == "One.\n\nTwo."
    with pytest.raises(ValueError, match="article p"):
        spider._extract_record(response.replace(body=b"<title>Series title</title>"))
