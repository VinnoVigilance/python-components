"""Unit tests for MediaSpider field extraction (multiple / prefix), the
full-path SourceRecordId regex, and filename-safe record ids."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from scrapy.http import HtmlResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider

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
