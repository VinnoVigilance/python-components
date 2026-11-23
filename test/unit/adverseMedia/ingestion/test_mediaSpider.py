"""Unit tests for MediaSpider field extraction (multiple / prefix), the
full-path SourceRecordId regex, and filename-safe record ids."""

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from scrapy.http import HtmlResponse, Response

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from ingestion.crawler.storage import CrawlerStorage
from services.adverseMediaPipeline import mediaTodoService
from ingestion.crawler.crawler import (
    _finalize_media_completion,
)
from services.adverseMediaPipeline.mediaIdentityService import (
    MediaIdentityService,
)

pytestmark = pytest.mark.unit

MEDIA_CONFIG_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "mediaSources.yaml"
)


HTML = """
<div class="entry-content">
  <div class="flourish-embed flourish-table" data-src="visualisation/111"></div>
  <div class="flourish-embed flourish-table" data-src="visualisation/222"></div>
  <div class="flourish-embed flourish-table" data-src="visualisation/111"></div>
  <div class="flourish-embed flourish-chart" data-src="visualisation/333"></div>
  <iframe src="https://datawrapper.dwcdn.net/abc/1/"></iframe>
  <object class="wp-block-file__embed" data="https://x/doc.pdf"></object>
</div>
"""


def _spider():
    return MediaSpider(
        task=None,
        source_config={},
        storage=None,
        records=[],
        discovery_service=None,
    )


def _response():
    return HtmlResponse(
        url="https://pcij.org/2025/10/29/slug/",
        body=HTML.encode("utf-8"),
        encoding="utf-8",
    )


class TestExtractField:
    def test_multiple_with_prefix_dedups_and_prepends(self):
        spider = _spider()
        value = spider._extract_field(
            _response(),
            {
                "selector": ".entry-content .flourish-table",
                "output": "attribute",
                "attribute": "data-src",
                "multiple": True,
                "prefix": "https://public.flourish.studio/",
            },
        )
        assert value == [
            "https://public.flourish.studio/visualisation/111",
            "https://public.flourish.studio/visualisation/222",
        ]

    def test_multiple_iframe_src_returns_all(self):
        spider = _spider()
        value = spider._extract_field(
            _response(),
            {
                "selector": ".entry-content iframe",
                "output": "attribute",
                "attribute": "src",
                "multiple": True,
            },
        )
        assert value == ["https://datawrapper.dwcdn.net/abc/1/"]

    def test_single_value_unchanged_takes_first_only(self):
        spider = _spider()
        value = spider._extract_field(
            _response(),
            {
                "selector": ".entry-content .flourish-table",
                "output": "attribute",
                "attribute": "data-src",
            },
        )
        assert value == "visualisation/111"

    def test_prefix_applies_to_scalar(self):
        spider = _spider()
        value = spider._extract_field(
            _response(),
            {
                "selector": ".entry-content .flourish-chart",
                "output": "attribute",
                "attribute": "data-src",
                "prefix": "https://public.flourish.studio/",
            },
        )
        assert value == "https://public.flourish.studio/visualisation/333"

    def test_no_match_returns_none(self):
        spider = _spider()
        value = spider._extract_field(
            _response(),
            {
                "selector": ".entry-content .nope",
                "output": "attribute",
                "attribute": "data-src",
                "multiple": True,
            },
        )
        assert value is None


# PCIJ's listing pages carry a hidden "recommended article" popup block that
# reuses the exact same `entry-title` markup as real listed articles, but
# lives outside the page's #main content area. link_selector must be scoped
# so the popup is never picked up as a discovered article.
LISTING_HTML_WITH_DECOY = """
<html><body>
  <main id="main" class="site-main">
    <article>
      <h2 class="entry-title"><a href="/2026/09/06/real-article/">Real</a></h2>
    </article>
  </main>
  <div class="newspack-popup-container newspack-lightbox hidden">
    <article>
      <h2 class="entry-title"><a href="/2025/10/29/decoy-popup-ad/">Decoy</a></h2>
    </article>
  </div>
</body></html>
"""


def _pcij_source_config(dataset_name):
    with MEDIA_CONFIG_PATH.open(encoding="utf-8") as handle:
        media_config = yaml.safe_load(handle)
    return media_config["sources"][dataset_name]


class TestPcijListingSelectorExcludesPopup:
    """Regression guard for the #main scoping fix: locks in the real
    config's link_selector against a page shaped like PCIJ's, so a future
    change back to a bare `h2.entry-title a` fails this test."""

    @pytest.mark.parametrize(
        "dataset_name",
        ["PCIJ_CORRUPTION_WATCH", "PCIJ_INVESTIGATIVE_REPORTS"],
    )
    def test_only_the_main_content_article_is_discovered(self, dataset_name):
        source_config = _pcij_source_config(dataset_name)

        discovery_service = MagicMock()
        discovery_service.build_record_key.side_effect = (
            lambda record: record["SourceURL"]
        )
        discovery_service.check_record_key.return_value = (False, False)

        spider = MediaSpider(
            task=None,
            source_config=source_config,
            storage=None,
            records=[],
            discovery_service=discovery_service,
        )

        response = HtmlResponse(
            url="https://pcij.org/category/corruption-watch/",
            body=LISTING_HTML_WITH_DECOY.encode("utf-8"),
            encoding="utf-8",
        )

        requests = [
            item
            for item in spider.parse_listing(response, page_number=1)
            if hasattr(item, "url")
        ]

        urls = [request.url for request in requests]

        assert any("real-article" in url for url in urls)
        assert not any("decoy-popup-ad" in url for url in urls)


class TestSourceRecordIdRegex:
    def test_full_path_after_domain_is_captured(self):
        value = MediaSpider._apply_extraction(
            "https://pcij.org/2025/10/29/slug/",
            {"strategy": "regex", "pattern": r"//[^/]+/(.+?)/*$"},
        )
        assert value == "2025/10/29/slug"


class TestBuildFileNameId:
    def test_filename_is_deterministic_hash_of_record_key(self):
        first = MediaSpider._build_file_name_id(
            record_key="SRC|DATA|stable",
        )
        second = MediaSpider._build_file_name_id(
            record_key="SRC|DATA|stable",
        )

        assert first == second
        assert len(first) == 24

    def test_different_record_keys_have_different_filenames(self):
        assert MediaSpider._build_file_name_id(
            record_key="SRC|DATA|one",
        ) != MediaSpider._build_file_name_id(
            record_key="SRC|DATA|two",
        )


def _listing_spider(policy):
    discovery_service = MagicMock()
    discovery_service.build_record_key.return_value = "SRC|DATA|1"
    discovery_service.check_record_key.return_value = (
        True,
        False,
    )

    spider = MediaSpider(
        task=None,
        source_config={
            "discovery": {
                "policy": policy,
                "start_page": 1,
                "pagination": {"type": "none"},
                "article": {
                    "link_selector": "a.news",
                },
            },
            "extraction": {
                "SourceRecordId": {
                    "source": "response_url",
                    "extraction": {
                        "strategy": "regex",
                        "pattern": r"[?&]id=([^&]+)",
                    },
                },
            },
        },
        storage=None,
        records=[],
        discovery_service=discovery_service,
    )

    response = HtmlResponse(
        url="https://example.test/news",
        body=b'<a class="news" href="detail?id=1">One</a>',
        encoding="utf-8",
    )

    return spider, response


def test_stop_after_known_skips_known_detail_download():
    spider, response = _listing_spider(
        "stop_after_known"
    )

    assert list(
        spider.parse_listing(response, page_number=1)
    ) == []


def test_full_scan_downloads_known_detail_for_change_detection():
    spider, response = _listing_spider(
        "full_scan"
    )

    outputs = list(
        spider.parse_listing(response, page_number=1)
    )

    assert len(outputs) == 1
    assert outputs[0].url == (
        "https://example.test/detail?id=1"
    )


def test_empty_first_listing_is_marked_incomplete():
    discovery_service = MagicMock()
    spider = MediaSpider(
        task=None,
        source_config={
            "discovery": {
                "policy": "stop_after_known",
                "start_page": 1,
                "pagination": {"type": "none"},
                "article": {
                    "link_selector": "a.news",
                },
            },
        },
        storage=None,
        records=[],
        discovery_service=discovery_service,
    )
    response = HtmlResponse(
        url="https://example.test/news",
        body=b"<html><body>No matching links</body></html>",
        encoding="utf-8",
    )

    assert list(
        spider.parse_listing(response, page_number=1)
    ) == []
    discovery_service.mark_discovery_failure.assert_called_once_with(
        "EMPTY_FIRST_LISTING_PAGE"
    )
    discovery_service.mark_source_end.assert_not_called()


def _paged_spider(pagination):
    discovery_service = MagicMock()
    discovery_service.build_record_key.return_value = "SRC|DATA|1"
    discovery_service.check_record_key.return_value = (False, False)

    spider = MediaSpider(
        task=None,
        source_config={
            "discovery": {
                "policy": "full_scan",
                "start_page": 1,
                "pagination": pagination,
                "article": {"link_selector": "a.news"},
            },
        },
        storage=None,
        records=[],
        discovery_service=discovery_service,
    )
    return spider, discovery_service


PAGE_NUMBER = {
    "type": "page_number",
    "url_pattern": "https://example.test/news/page/{page}/",
}


def _next_page_request(spider):
    response = HtmlResponse(
        url="https://example.test/news/",
        body=b'<a class="news" href="/a/">A</a>',
        encoding="utf-8",
    )
    return [
        output
        for output in spider.parse_listing(response, page_number=1)
        if output.callback == spider.parse_listing
    ][0]


class TestPaginationEndStatus:
    """A source that declares pagination.end_status treats that status past page 1 as the source end."""

    def test_end_status_page_marks_source_end(self):
        spider, discovery_service = _paged_spider(
            {**PAGE_NUMBER, "end_status": 404}
        )
        response = HtmlResponse(
            url="https://example.test/news/page/4/",
            status=404,
            body=b"<html>Not found</html>",
            encoding="utf-8",
        )

        assert list(spider.parse_listing(response, page_number=4)) == []
        discovery_service.mark_source_end.assert_called_once()
        discovery_service.mark_discovery_failure.assert_not_called()

    def test_next_page_request_lets_end_status_through(self):
        spider, _ = _paged_spider({**PAGE_NUMBER, "end_status": 404})

        request = _next_page_request(spider)

        assert request.url == "https://example.test/news/page/2/"
        assert request.meta["handle_httpstatus_list"] == [404]

    def test_without_end_status_next_page_request_is_unchanged(self):
        spider, _ = _paged_spider(PAGE_NUMBER)

        request = _next_page_request(spider)

        assert "handle_httpstatus_list" not in request.meta

    @pytest.mark.parametrize(
        "dataset_name",
        ["PCIJ_CORRUPTION_WATCH", "PCIJ_INVESTIGATIVE_REPORTS"],
    )
    def test_pcij_config_declares_404_as_end(self, dataset_name):
        pagination = _pcij_source_config(dataset_name)["discovery"]["pagination"]

        assert pagination["end_status"] == 404


def _stable_doj_listing_spider():
    discovery_service = MagicMock()
    discovery_service.check_record_key.return_value = (
        False,
        False,
    )

    source_config = {
        "source_name": "DOJ_PH",
        "dataset_name": "DOJ_PH_NEWS",
        "identity": {
            "record_key": {
                "fields": [
                    "source_name",
                    "dataset_name",
                    "ListingPublishedDate",
                    "ListingTitle",
                ],
                "hash": True,
            },
        },
        "discovery": {
            "policy": "stop_after_known",
            "start_page": 1,
            "pagination": {"type": "none"},
            "article": {
                "link_selector": "td[align='justify'] > a",
                "identity_fields": {
                    "ListingPublishedDate": {
                        "source": "xpath",
                        "selectors": [
                            "ancestor::tr[1]/td[1]//text()",
                        ],
                    },
                    "ListingTitle": {
                        "source": "link_text",
                        "required": False,
                    },
                },
            },
        },
        "extraction": {},
    }

    discovery_service.build_record_key.side_effect = (
        lambda record: MediaIdentityService.generate_record_key(
            source_config=source_config,
            record=record,
        )
    )

    spider = MediaSpider(
        task=None,
        source_config=source_config,
        storage=None,
        records=[],
        discovery_service=discovery_service,
    )

    return spider


def test_doj_record_key_ignores_rotating_newsid():
    old_response = HtmlResponse(
        url="https://www.doj.gov.ph/news.html",
        body=(
            b"<table><tr>"
            b"<td>09/11/2026</td>"
            b"<td align='justify'>"
            b"<a href='news_article.html?newsid=old-token'>"
            b"Same DOJ Article</a></td>"
            b"</tr></table>"
        ),
        encoding="utf-8",
    )

    new_response = HtmlResponse(
        url="https://www.doj.gov.ph/news.html",
        body=(
            b"<table><tr>"
            b"<td>09/11/2026</td>"
            b"<td align='justify'>"
            b"<a href='news_article.html?newsid=new-token'>"
            b"  Same   DOJ Article  </a></td>"
            b"</tr></table>"
        ),
        encoding="utf-8",
    )

    old_outputs = list(
        _stable_doj_listing_spider().parse_listing(
            old_response,
            page_number=1,
        )
    )
    new_outputs = list(
        _stable_doj_listing_spider().parse_listing(
            new_response,
            page_number=1,
        )
    )

    assert len(old_outputs) == 1
    assert len(new_outputs) == 1
    assert (
        old_outputs[0].cb_kwargs["record_key"]
        == new_outputs[0].cb_kwargs["record_key"]
    )
    assert old_outputs[0].cb_kwargs[
        "source_record_id"
    ] is None
    assert new_outputs[0].cb_kwargs[
        "source_record_id"
    ] is None
    assert "old-token" not in old_outputs[0].cb_kwargs[
        "record_key"
    ]


def test_missing_detail_results_make_crawl_incomplete():
    result = _finalize_media_completion(
        discovery_summary={
            "selected_detail_count": 603,
            "stop_reason": "SOURCE_EXHAUSTED",
            "completed_safely": True,
        },
        record_count=18,
    )

    assert result == (
        603,
        585,
        "DETAIL_RESULTS_INCOMPLETE",
        False,
    )


def _navigation_spider(tmp_path, *, document_type="html", replay=False):
    recipe = {
        "start_url": "https://example.test/search",
        "replay_parents": replay,
        "metadata": {"DiscoveryMethod": "archive"},
        "stages": [{
            "row_selector": "li", "row_key": ["NoticeId"],
            "fields": {"NoticeId": {"selector": "a", "required": True}},
            "action": {"type": "link", "selector": "a"},
        }],
        "document": {"type": document_type, "identity_fields": ["NoticeId"]},
    }
    config = {
        "source_name": "EXAMPLE", "dataset_name": "EXAMPLE_NOTICES",
        "discovery": {"strategy": "form_search", "form_search": recipe},
        "identity": {"record_key": {"fields": ["source_name", "NoticeId"], "hash": True}},
        "extraction": ({"Title": {"selector": "h1", "required": True},
                        "BodyText": {"selector": "article", "output": "html", "required": True}}
                       if document_type == "html" else {}),
    }
    service = SimpleNamespace(
        persist_pending_work=False, limit_reached=False, discovery_failure_count=0,
        check_record_key=MagicMock(return_value=(False, False)),
        record_detail_selected=MagicMock(), mark_discovery_failure=MagicMock(),
        mark_source_end=MagicMock(),
    )
    spider = MediaSpider(
        task=SimpleNamespace(timeout=30), source_config=config,
        storage=CrawlerStorage("EXAMPLE", "EXAMPLE_NOTICES", str(tmp_path), None),
        records=[], discovery_service=service,
    )
    return spider, recipe


def _navigation_pages(spider, pages):
    requests = []

    async def download(request):
        requests.append(request)
        body = pages[request.url]
        if body.startswith(b"%PDF-"):
            return Response(request.url, body=body, request=request)
        return HtmlResponse(request.url, body=body, encoding="utf-8", request=request)

    spider.crawler = SimpleNamespace(engine=SimpleNamespace(download_async=download))
    return requests


@pytest.mark.parametrize("document_type", ["html", "pdf"])
def test_navigation_saves_original_and_retains_parent_metadata(tmp_path, document_type):
    spider, _ = _navigation_spider(tmp_path, document_type=document_type)
    original = (b"<h1>Publication</h1><article>Full publication text</article>"
                if document_type == "html" else b"%PDF-1.4\noriginal bytes")
    _navigation_pages(spider, {
        "https://example.test/search": b'<ul><li><a href="/notice">N-1</a></li></ul>',
        "https://example.test/notice": original,
    })

    asyncio.run(spider.collect_form_searches())

    [record] = spider.records
    assert Path(record["detail_file_path"]).read_bytes() == original
    assert Path(record["detail_file_path"]).suffix == "." + document_type
    assert record["extracted"]["NoticeId"] == "N-1"
    assert record["extracted"]["SourceRecordId"] == "N-1"
    assert record["extracted"]["DiscoveryMethods"] == ["archive"]
    assert record["discovery_context"]["kind"] == "form_search"
    if document_type == "html":
        assert record["extracted"]["Title"] == "Publication"
        assert record["extracted"]["BodyText"] == "<article>Full publication text</article>"
        assert record["extracted"]["SourceURL"] == "https://example.test/notice"
    spider.discovery_service.mark_discovery_failure.assert_not_called()


def test_next_link_pages_replay_the_correct_page_and_keep_its_metadata(tmp_path):
    spider, recipe = _navigation_spider(tmp_path, replay=True)
    recipe["stages"][0].update(
        pagination={"selector": "a.next"},
        page_fields={"PageTitle": {"selector": "h2"}},
    )
    requests = _navigation_pages(spider, {
        "https://example.test/search": b'<h2>First</h2><li><a href="/one">1</a></li><a class="next" href="?page=2">Next</a>',
        "https://example.test/search?page=2": b'<h2>Second</h2><li><a href="/two">2</a></li>',
        "https://example.test/one": b"<h1>One</h1><article>First body</article>",
        "https://example.test/two": b"<h1>Two</h1><article>Second body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    assert [(r["extracted"]["NoticeId"], r["extracted"]["PageTitle"])
            for r in spider.records] == [("1", "First"), ("2", "Second")]
    assert [r.url for r in requests].count("https://example.test/search?page=2") == 2
    spider.discovery_service.mark_discovery_failure.assert_not_called()


@pytest.mark.parametrize("href", ["/search", "https://other.test/page", ""])
def test_invalid_next_link_keeps_successful_records_but_fails_search(tmp_path, href):
    spider, recipe = _navigation_spider(tmp_path)
    recipe["stages"][0]["pagination"] = {"selector": "a.next"}
    _navigation_pages(spider, {
        "https://example.test/search": (
            '<li><a href="/one">1</a></li><a class="next" href="' + href + '">Next</a>'
        ).encode(),
        "https://example.test/one": b"<h1>One</h1><article>Body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    assert len(spider.records) == 2
    assert spider.records[0]["extracted"]["NoticeId"] == "1"
    assert spider.records[1]["failed"] is True
    assert spider.records[1]["discovery_retry_item"]["recipe"] == recipe
    spider.discovery_service.mark_discovery_failure.assert_called_once_with("FORM_SEARCH_INCOMPLETE")


def test_multi_stage_forms_use_fresh_hidden_fields_and_preserve_metadata(tmp_path):
    spider, recipe = _navigation_spider(tmp_path, document_type="pdf", replay=True)
    recipe["search_action"] = {"form_xpath": "//form", "fields": {"query": "{number}"}}
    recipe["parameters"] = {"number": {"values": [12], "format": "05d"}}
    recipe["stages"] = [
        {"row_selector": "form", "row_key": ["CaseNumber"],
         "fields": {"CaseNumber": {"selector": "span", "required": True}},
         "action": {"submit_selector": "button"}},
        {"row_selector": "form", "row_key": ["NoticeId"],
         "page_fields": {"CaseStatus": {"selector": "h2"}},
         "fields": {"NoticeId": {"selector": "span", "required": True}},
         "action": {"submit_selector": "button"}},
    ]
    tokens = []

    async def download(request):
        if request.method == "GET":
            token = str(len(tokens) + 1)
            tokens.append(token)
            html = f'<form method="post"><input name="token" value="{token}"><input name="query"></form>'
        else:
            fields = parse_qs(request.body.decode(), keep_blank_values=True)
            assert fields["token"] == [tokens[-1]]
            if "query" in fields:
                assert fields["query"] == ["00012"]
                html = f'<form method="post"><input name="token" value="{tokens[-1]}"><span>Case A</span><button name="case">Open</button></form>'
            elif "case" in fields:
                html = f'<h2>Closed</h2><form method="post"><input name="token" value="{tokens[-1]}"><span>Decision A</span><button name="document">Download</button></form>'
            else:
                assert "document" in fields
                return Response(request.url, body=b"%PDF-1.4\ncase decision", request=request)
        return HtmlResponse(request.url, body=html.encode(), encoding="utf-8", request=request)

    spider.crawler = SimpleNamespace(engine=SimpleNamespace(download_async=download))
    asyncio.run(spider.collect_form_searches())

    [record] = spider.records
    assert record["extracted"]["CaseNumber"] == "Case A"
    assert record["extracted"]["CaseStatus"] == "Closed"
    assert record["extracted"]["NoticeId"] == "Decision A"
    assert len(tokens) == 3  # Search, case selection replay, publication replay.
    assert spider.discovery_service.stop_reason == "CONFIGURED_RANGE_COMPLETE"


def test_no_db_navigation_never_reads_or_writes_pending_work(tmp_path, monkeypatch):
    spider, _ = _navigation_spider(tmp_path)
    for name in ("load_items", "queue_items", "remove_items"):
        monkeypatch.setattr(mediaTodoService, name, MagicMock(side_effect=AssertionError(name)))
    _navigation_pages(spider, {
        "https://example.test/search": b'<li><a href="/one">1</a></li>',
        "https://example.test/one": b"<h1>One</h1><article>Body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    assert len(spider.records) == 1
    assert not spider.records[0].get("failed")


def test_failed_branch_stays_pending_while_successful_publication_is_saved(tmp_path, monkeypatch):
    spider, _ = _navigation_spider(tmp_path)
    spider.persist_pending_work = True
    queue = MagicMock()
    remove = MagicMock()
    monkeypatch.setattr(mediaTodoService, "load_items", lambda _: [])
    monkeypatch.setattr(mediaTodoService, "queue_items", queue)
    monkeypatch.setattr(mediaTodoService, "remove_items", remove)
    _navigation_pages(spider, {
        "https://example.test/search": b'<li><a href="/bad">bad</a></li><li><a href="/good">good</a></li>',
        "https://example.test/bad": b"<h1>Missing body</h1>",
        "https://example.test/good": b"<h1>Good</h1><article>Body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    assert spider.records[0]["extracted"]["NoticeId"] == "good"
    assert spider.records[1]["failed"] is True
    assert [call.args[1][0]["kind"] for call in queue.call_args_list] == ["form_search", "process"]
    remove.assert_not_called()
    # The bad publication's original HTML remains available for investigation.
    assert any("Missing body" in p.read_text() for p in spider.storage.detail_path.glob("*.html"))


def test_pending_search_removed_only_after_all_publications_saved(tmp_path, monkeypatch):
    spider, _ = _navigation_spider(tmp_path)
    spider.persist_pending_work = True
    events = []
    monkeypatch.setattr(mediaTodoService, "load_items", lambda _: [])
    monkeypatch.setattr(mediaTodoService, "queue_items", lambda _, items: events.append(items[0]["kind"]))
    monkeypatch.setattr(mediaTodoService, "remove_items", lambda *_: events.append("removed"))
    _navigation_pages(spider, {
        "https://example.test/search": b'<li><a href="/one">1</a></li>',
        "https://example.test/one": b"<h1>One</h1><article>Body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    assert events == ["form_search", "process", "removed"]


def test_stage_fields_use_parent_url_xpath_and_value_mapping(tmp_path):
    spider, _ = _navigation_spider(tmp_path)
    response = HtmlResponse("https://example.test/results/", body=b'<li><a href="doc.pdf">MANILA</a></li>', encoding="utf-8")
    fields = {
        "Station": {"selector_type": "xpath", "selector": ".//a", "values": {"MANILA": "mnl"}, "required": True},
        "PDFURL": {"selector": "a", "output": "attribute", "attribute": "href"},
        "ParentURL": {"source": "response_url"},
    }

    assert spider._extract_record(response.css("li")[0], fields=fields, base_url=response.url) == {
        "Station": "mnl", "PDFURL": "https://example.test/results/doc.pdf", "ParentURL": response.url,
    }
    assert spider._extract_record(response, fields={}) == {}
    with pytest.raises(ValueError, match="Required extraction field missing"):
        spider._extract_record(response)  # Source-level required HTML fields still apply.


@pytest.mark.parametrize("body", [b"<h1>Error page</h1>", b"not a PDF"])
def test_expected_pdf_rejects_other_content(tmp_path, body):
    spider, recipe = _navigation_spider(tmp_path, document_type="pdf")
    with pytest.raises(ValueError, match="configured PDF"):
        spider._save_form_publication(Response("https://example.test/document", body=body),
                                      {"recipe": recipe}, {"NoticeId": "1"})
    assert spider.records == []
    assert list(spider.storage.detail_path.glob("*.pdf")) == []


def test_html_detail_default_path_still_saves_original_before_extraction_failure(tmp_path):
    spider, _ = _navigation_spider(tmp_path)
    response = HtmlResponse("https://example.test/notice", body=b"<h1>No body</h1>", encoding="utf-8")

    [record] = list(spider.parse_detail(response, "key", False, None))

    assert record["failed"] is True
    assert len(list(spider.storage.detail_path.glob("*.html"))) == 1


def test_multiple_recipes_deduplicate_publications_and_share_metadata(tmp_path):
    spider, first = _navigation_spider(tmp_path)
    second = deepcopy(first)
    second["start_url"] = "https://example.test/recent"
    spider.discovery_config["form_search"] = [first, second]
    _navigation_pages(spider, {
        "https://example.test/search": b'<li><a href="/one">1</a></li>',
        "https://example.test/recent": b'<li><a href="/one">1</a></li>',
        "https://example.test/one": b"<h1>One</h1><article>Body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    assert len(spider.records) == 1
    spider.discovery_service.check_record_key.assert_called_once()


def test_navigation_rejects_unbounded_search_before_requests(tmp_path):
    spider, recipe = _navigation_spider(tmp_path)
    recipe["parameters"] = {"number": {"range": {"start": 1, "end": None}}}
    with pytest.raises(ValueError, match="explicit inclusive range"):
        asyncio.run(spider.collect_form_searches())


def test_request_guard_rejects_cross_origin_and_http_errors(tmp_path):
    import scrapy

    spider, recipe = _navigation_spider(tmp_path)
    engine = SimpleNamespace(download_async=AsyncMock(return_value=Response(recipe["start_url"], status=500)))
    spider.crawler = SimpleNamespace(engine=engine)
    item = {"recipe": recipe}
    with pytest.raises(ValueError, match="configured origin"):
        asyncio.run(spider._download_form_request(scrapy.Request("https://other.test/"), item))
    engine.download_async.assert_not_called()
    with pytest.raises(ValueError, match="Unexpected form response"):
        asyncio.run(spider._download_form_request(scrapy.Request(recipe["start_url"]), item))


def test_invalid_row_does_not_block_valid_sibling(tmp_path):
    spider, _ = _navigation_spider(tmp_path)
    _navigation_pages(spider, {
        "https://example.test/search": b'<li>Missing link and identity</li><li><a href="/good">good</a></li>',
        "https://example.test/good": b"<h1>Good</h1><article>Body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    assert spider.records[0]["extracted"]["NoticeId"] == "good"
    assert spider.records[1]["failed"] is True


@pytest.mark.parametrize("allow_identical,expected_count", [(False, 0), (True, 1)])
def test_identical_rows_are_only_collapsed_when_configured(tmp_path, allow_identical, expected_count):
    spider, recipe = _navigation_spider(tmp_path)
    recipe["stages"][0]["allow_identical_rows"] = allow_identical
    _navigation_pages(spider, {
        "https://example.test/search": b'<li><a href="/one">1</a></li><li><a href="/one">1</a></li>',
        "https://example.test/one": b"<h1>One</h1><article>Body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    assert len([r for r in spider.records if not r.get("failed")]) == expected_count
    assert any(r.get("failed") for r in spider.records) is (not allow_identical)


def test_conflicting_metadata_rows_are_preserved_for_one_publication(tmp_path):
    spider, recipe = _navigation_spider(tmp_path)
    recipe["stages"][0]["fields"]["ListingTitle"] = {"selector": "span"}
    recipe["stages"][0]["row_key"] = ["NoticeId", "ListingTitle"]
    recipe["document"]["conflict_fields"] = ["ListingTitle"]
    _navigation_pages(spider, {
        "https://example.test/search": b'<li><a href="/one">1</a><span>First</span></li><li><a href="/one">1</a><span>Second</span></li>',
        "https://example.test/one": b"<h1>One</h1><article>Body</article>",
    })

    asyncio.run(spider.collect_form_searches())

    [record] = spider.records
    assert record["extracted"]["SourceMetadataConflict"] is True
    assert [row["ListingTitle"] for row in record["extracted"]["SourceRows"]] == ["First", "Second"]
