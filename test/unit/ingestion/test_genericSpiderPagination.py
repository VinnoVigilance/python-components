import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import scrapy
from scrapy.http import HtmlResponse

from ingestion.crawler.pagination import PaginationError
from ingestion.crawler.spiders.genericSpider import GenericSpider


BASE = "https://example.test/alerts"


def response(page=0, next_href=None, name="Company", extra_next=""):
    link = "" if next_href is None else (
        '<a rel="next" href="' + next_href + '">Next</a>'
    )
    body = (
        '<html><body><div class="row"><a href="/profiles/'
        + str(page) + '">' + name + '</a></div>'
        + link + extra_next + '</body></html>'
    )
    return HtmlResponse(
        url=BASE + "?page=" + str(page),
        body=body.encode(), encoding="utf-8",
    )


def build_spider(pagination=None):
    state = {"complete": False, "error": None}
    storage = Mock()
    config = {
        "discovery": {
            "row_selector": ".row",
            "detail_link_selector": "a",
        },
        "list_fields": {"name": {"selector": "a", "value": "text"}},
        "detail_fields": {"name": {"selector": "h1", "value": "text"}},
        "record_id": {"strategy": "url_regex", "pattern": r"/([^/]+)$"},
        "storage": {"save_listing_page": True},
    }
    if pagination is not None:
        config["pagination"] = pagination
    records = []
    spider = GenericSpider(
        task=SimpleNamespace(url=BASE, detail_items=None),
        crawler_config=config, storage=storage, records=records,
        pagination_state=state,
    )
    return spider, storage, state


def paginated(**overrides):
    return {"type": "next_link", "next_link_selector": "a[rel=next]",
            **overrides}


class TestGenericSpiderPagination(unittest.TestCase):
    def test_first_page_requests_details_and_next_page(self):
        spider, storage, state = build_spider(paginated())
        requests = list(spider.parse(response(next_href="?page=1")))
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url, "https://example.test/profiles/0")
        self.assertEqual(requests[0].callback, spider.parse_detail)
        self.assertEqual(requests[1].url, BASE + "?page=1")
        self.assertEqual(requests[1].callback, spider.parse)
        self.assertEqual(requests[1].errback, spider.listing_failed)
        self.assertFalse(state["complete"])
        storage.save_source_html.assert_not_called()

    def test_two_pages_save_combined_source_only_at_end(self):
        spider, storage, state = build_spider(paginated())
        list(spider.parse(response(next_href="?page=1", name="First")))
        requests = list(spider.parse(response(1, name="Second")))
        self.assertEqual(len(requests), 1)
        self.assertTrue(state["complete"])
        storage.save_source_html.assert_called_once()
        combined = storage.save_source_html.call_args.args[0]
        self.assertIn("First", combined)
        self.assertIn("Second", combined)
        self.assertEqual(combined.count('data-listing-url='), 2)

    def test_details_from_both_pages_are_collected(self):
        spider, _, state = build_spider(paginated())
        requests = list(spider.parse(response(next_href="?page=1")))
        requests += list(spider.parse(response(1)))
        for request in requests:
            if request.callback != spider.parse_detail:
                continue
            detail = HtmlResponse(
                url=request.url, body=b"<html><h1>Profile</h1></html>",
                encoding="utf-8",
            )
            list(request.callback(detail, **request.cb_kwargs))
        self.assertEqual(len(spider.records), 2)
        self.assertTrue(state["complete"])
        self.assertEqual(len(spider.expected_details), 2)

    def test_old_sources_do_not_follow_next_links(self):
        spider, storage, _ = build_spider()
        page = response(next_href="?page=1")
        requests = list(spider.parse(page))
        self.assertEqual(len(requests), 1)
        storage.save_source_html.assert_called_once_with(page.text)

    def test_query_params_start_url_is_unchanged(self):
        spider, _, _ = build_spider(
            {"type": "query_params", "params": {"items_per_page": "All"}}
        )
        self.assertEqual(
            spider._build_start_url(), BASE + "?items_per_page=All"
        )

    def test_page_failure_marks_pagination_incomplete(self):
        spider, storage, state = build_spider(paginated())
        list(spider.parse(response(next_href="?page=1")))
        failure = SimpleNamespace(
            request=SimpleNamespace(url=BASE + "?page=1"),
            getErrorMessage=lambda: "HTTP 503",
        )
        spider.listing_failed(failure)
        self.assertFalse(state["complete"])
        self.assertIn("HTTP 503", state["error"])
        storage.save_source_html.assert_not_called()

    def test_missing_detail_remains_tracked(self):
        spider, _, _ = build_spider(paginated())
        list(spider.parse(response()))
        self.assertEqual(len(spider.expected_details), 1)
        self.assertEqual(spider.records, [])

    def test_missing_selector_is_an_error(self):
        spider, storage, state = build_spider({"type": "next_link"})
        with self.assertRaises(PaginationError):
            list(spider.parse(response()))
        self.assertFalse(state["complete"])
        self.assertIn("next_link_selector", state["error"])
        storage.save_source_html.assert_not_called()

    def test_self_link_does_not_complete(self):
        spider, storage, state = build_spider(paginated())
        with self.assertRaises(PaginationError):
            list(spider.parse(response(next_href="?page=0")))
        self.assertFalse(state["complete"])
        storage.save_source_html.assert_not_called()

    def test_cycle_to_previous_page_is_an_error(self):
        spider, _, state = build_spider(paginated())
        list(spider.parse(response(next_href="?page=1")))
        with self.assertRaises(PaginationError):
            list(spider.parse(response(1, next_href="?page=0")))
        self.assertFalse(state["complete"])

    def test_cross_origin_next_link_is_an_error(self):
        spider, _, state = build_spider(paginated())
        with self.assertRaises(PaginationError):
            list(spider.parse(response(next_href="https://other.test/list")))
        self.assertFalse(state["complete"])

    def test_duplicate_identical_next_links_are_allowed(self):
        spider, _, _ = build_spider(paginated())
        requests = list(spider.parse(response(
            next_href="?page=1",
            extra_next='<a rel="next" href="?page=1">Next again</a>',
        )))
        self.assertEqual(len(requests), 2)

    def test_conflicting_next_links_are_an_error(self):
        spider, _, state = build_spider(paginated())
        with self.assertRaises(PaginationError):
            list(spider.parse(response(
                next_href="?page=1",
                extra_next='<a rel="next" href="?page=2">Wrong</a>',
            )))
        self.assertFalse(state["complete"])

    def test_max_pages_does_not_silently_truncate(self):
        spider, storage, state = build_spider(paginated(max_pages=1))
        with self.assertRaises(PaginationError):
            list(spider.parse(response(next_href="?page=1")))
        self.assertFalse(state["complete"])
        storage.save_source_html.assert_not_called()

    def test_xpath_next_link_selector(self):
        spider, _, _ = build_spider(paginated(
            next_link_selector="//a[@rel='next']",
            next_link_selector_type="xpath",
        ))
        self.assertEqual(len(list(spider.parse(
            response(next_href="?page=1")
        ))), 2)

    def test_next_link_without_href_is_an_error(self):
        spider, _, state = build_spider(paginated())
        with self.assertRaises(PaginationError):
            list(spider.parse(response(
                extra_next='<a rel="next">Broken next</a>'
            )))
        self.assertFalse(state["complete"])

    def test_parse_error_is_recorded(self):
        spider, storage, state = build_spider(paginated())
        spider.config["discovery"].pop("row_selector")
        with self.assertRaises(ValueError):
            list(spider.parse(response()))
        self.assertFalse(state["complete"])
        self.assertIn("row_selector", state["error"])
        storage.save_source_html.assert_not_called()

    def test_paginated_start_request_has_error_callback(self):
        spider, _, _ = build_spider(paginated())

        async def collect():
            return [item async for item in spider.start()]

        requests = asyncio.run(collect())
        self.assertEqual(requests[0].errback, spider.listing_failed)

    def test_old_start_request_is_unchanged(self):
        spider, _, _ = build_spider()

        async def collect():
            return [item async for item in spider.start()]

        requests = asyncio.run(collect())
        self.assertIsNone(requests[0].errback)


if __name__ == "__main__":
    unittest.main()

