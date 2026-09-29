"""Unit tests for detail-page tracking: missing/broken pages and detail-items retry mode."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import HtmlResponse

from ingestion.crawler.crawler import _find_missing_details, _keep_broken_as_list_only
from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.spiders.genericSpider import GenericSpider
from ingestion.crawler.spiders.mediaSpider import MediaSpider
from ingestion.crawler.spiders.savedHtmlSpider import SavedHtmlSpider

pytestmark = pytest.mark.unit


def _task(detail_items=None):
    return CrawlerTask(
        url="https://x/list",
        source_name="SRC",
        list_name="LIST",
        detail_items=detail_items,
    )


def _failure(url, cb_kwargs, message, status=None):
    response = SimpleNamespace(status=status) if status else None
    return SimpleNamespace(
        request=SimpleNamespace(url=url, cb_kwargs=cb_kwargs),
        value=SimpleNamespace(response=response),
        getErrorMessage=lambda: message,
    )


def _item(record_id):
    return {"record_id": record_id, "detail_url": f"https://x/{record_id}", "list_data": {"n": record_id}}


def test_find_missing_details_compares_queued_pages_with_records():
    expected = [_item("1"), _item("2"), _item(3)]
    records = [{"source_record_id": "1"}, {"source_record_id": 3}]

    count, missing = _find_missing_details(expected, records)

    assert count == 3
    assert missing == [_item("2")]


def test_broken_pages_become_list_only_records_and_are_not_missing():
    gone = {**_item("2"), "permanent": True, "error": "HTTP 404"}
    slow = {**_item("3"), "permanent": False, "error": "timeout"}
    records = [{"source_record_id": "1"}]

    broken = _keep_broken_as_list_only([_item("1"), gone, slow], records)
    _, missing = _find_missing_details([_item("1"), gone, slow], records)

    assert broken == [gone]
    assert records[1] == {
        "source_record_id": "2", "list": {"n": "2"}, "detail": {}, "attachments": [],
        "detail_url": "https://x/2", "detail_error": "HTTP 404",
    }
    assert missing == [slow]


class TestGenericSpider:
    def _spider(self, detail_items=None):
        expected = []
        spider = GenericSpider(
            task=_task(detail_items), crawler_config={}, storage=None,
            records=[], expected_details=expected,
        )
        return spider, expected

    def test_detail_items_mode_requests_only_those_pages(self):
        spider, expected = self._spider([_item("2"), _item("5")])

        requests = list(spider._detail_item_requests())

        assert [r.url for r in requests] == ["https://x/2", "https://x/5"]
        assert all(r.dont_filter for r in requests)
        assert requests[0].cb_kwargs == {"list_data": {"n": "2"}, "record_id": "2",
                                         "detail_url": "https://x/2"}
        assert [item["record_id"] for item in expected] == ["2", "5"]

    def test_listing_requests_skip_dupe_filter_and_repeat_ids(self):
        spider = GenericSpider(
            task=_task(), storage=None, records=[], expected_details=[],
            crawler_config={
                "discovery": {"row_selector": "tr", "detail_link_selector": "a",
                              "detail_link_attribute": "href"},
                "record_id": {"strategy": "url_regex", "source": "detail_url",
                              "pattern": "/node/(\\d+)"},
            },
        )
        body = (b"<table><tr><td><a href='/node/1'>A</a></td></tr>"
                b"<tr><td><a href='//x/node/1'>A</a></td></tr>"
                b"<tr><td><a href='/node/2'>B</a></td></tr></table>")
        response = HtmlResponse(url="https://x/list", body=body, encoding="utf-8")

        requests = list(spider.parse(response))

        assert [r.cb_kwargs["record_id"] for r in requests] == ["1", "2"]
        assert all(r.dont_filter for r in requests)

    @pytest.mark.parametrize("status, message, permanent", [
        (404, "Ignoring non-200 response", True),
        (410, "Ignoring non-200 response", True),
        (503, "Ignoring non-200 response", False),
        (None, "Maximum number of redirections reached (max redirections)", True),
        (None, "User timeout caused connection failure", False),
    ])
    def test_detail_failed_marks_broken_on_source(self, status, message, permanent):
        spider, expected = self._spider()
        spider._expect_detail("7", "https://x/7", {})

        spider.detail_failed(_failure("https://x/7", {"record_id": "7"}, message, status))

        assert expected[0]["permanent"] is permanent
        assert expected[0]["error"] == (f"HTTP {status}" if status else message)


class TestSavedHtmlSpider:
    def _spider(self, detail_items=None, strategy="browser"):
        expected = []
        spider = SavedHtmlSpider(
            task=_task(detail_items),
            crawler_config={"detail_fetch_strategy": strategy, "storage": {}},
            storage=MagicMock(), records=[], expected_details=expected,
        )
        return spider, expected

    def test_failed_browser_detail_is_skipped_and_marked(self):
        spider, expected = self._spider()
        spider._expect_detail("7", "https://x/7", {})
        failed = {**_item("7"), "fetch_error": "navigation timeout"}
        fetcher = MagicMock()
        fetcher.fetch.return_value = [(failed, None)]

        with patch("ingestion.crawler.spiders.savedHtmlSpider.BrowserDetailFetcher",
                   return_value=fetcher):
            results = list(spider._fetch_browser_details([_item("7")]))

        assert results == []
        assert expected[0]["error"] == "navigation timeout"
        assert expected[0]["permanent"] is False

    def test_detail_items_without_browser_yield_requests(self):
        spider, expected = self._spider([_item("4")], strategy="direct")

        [request] = list(spider._iter_detail_items("direct"))

        assert request.url == "https://x/4"
        assert request.dont_filter
        assert request.errback == spider.detail_failed
        assert [item["record_id"] for item in expected] == ["4"]


class TestMediaSpider:
    def _spider(self, detail_items=None):
        return MediaSpider(
            task=_task(detail_items), source_config={}, storage=MagicMock(),
            records=[], discovery_service=None,
        )

    def test_detail_failed_keeps_a_retryable_record(self):
        spider = self._spider()
        kwargs = {"record_key": "k1", "is_known": False, "source_record_id": "s1",
                  "identity_fields": {"id": "s1"}}

        [record] = list(spider.detail_failed(_failure("https://x/a", kwargs, "timeout")))

        assert record["failed"] is True
        assert record["detail_url"] == "https://x/a"
        assert record["record_key"] == "k1"
        assert record["identity_fields"] == {"id": "s1"}
        assert record["error_stage"] == "DETAIL_FETCH"
        assert spider.records == [record]

    def test_extraction_error_becomes_failed_record(self):
        spider = self._spider()
        spider.storage.save_detail_html.return_value = "saved.html"
        spider._extract_record = MagicMock(side_effect=ValueError("bad page"))
        response = HtmlResponse(url="https://x/a", body=b"<html/>", encoding="utf-8")

        [record] = list(spider.parse_detail(response, record_key="k1", is_known=False,
                                            source_record_id=None))

        assert record["failed"] is True
        assert record["error_stage"] == "DETAIL_EXTRACTION"
        assert record["error_type"] == "ValueError"
        assert record["detail_url"] == "https://x/a"

    def test_detail_item_kwargs_treat_items_as_new(self):
        item = {"record_key": "k1", "detail_url": "https://x/a", "source_record_id": "s1"}

        assert MediaSpider.detail_item_kwargs(item) == {
            "record_key": "k1", "is_known": False,
            "source_record_id": "s1", "identity_fields": {},
        }
