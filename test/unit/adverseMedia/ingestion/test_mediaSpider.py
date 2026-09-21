"""Unit tests for MediaSpider field extraction (multiple / prefix), the
full-path SourceRecordId regex, and filename-safe record ids."""

import pytest
from scrapy.http import HtmlResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider

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
    def test_slashes_become_dashes(self):
        assert (
            MediaSpider._build_file_name_id(
                record_key="k", source_record_id="2025/10/29/slug"
            )
            == "2025-10-29-slug"
        )

    def test_plain_id_unchanged(self):
        assert (
            MediaSpider._build_file_name_id(
                record_key="k", source_record_id="10914"
            )
            == "10914"
        )
