"""Unit tests for MediaSpider field extraction (multiple / prefix), the
full-path SourceRecordId regex, and filename-safe record ids."""

import pytest
from scrapy.http import HtmlResponse
from unittest.mock import MagicMock

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from ingestion.crawler.crawler import (
    _finalize_media_completion,
)
from services.adverseMediaPipeline.mediaIdentityService import (
    MediaIdentityService,
)

pytestmark = pytest.mark.unit


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
