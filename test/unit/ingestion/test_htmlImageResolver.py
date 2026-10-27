"""Pure image discovery, with no browser/network dependency."""

import json
import unittest
from pathlib import Path

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from copy import deepcopy

from ingestion.urlResolver.htmlImageResolver import resolve_image_from_html


class TestHtmlImageResolver(unittest.TestCase):
    def setUp(self):
        self.url = "https://example.org/list/"
        self.config = {"selector": "//img[@id='poster']"}

    def resolve(self, attributes, config=None, prefix=""):
        return resolve_image_from_html(
            f"<html><head>{prefix}</head><body><img id='poster' {attributes}></body></html>",
            self.url, config or self.config,
        )

    def test_largest_width_descriptor_wins_independent_of_order(self):
        result = self.resolve("src='/small.jpg' srcset='/medium.jpg 800w, /original.jpg 1277w, /small.jpg 300w'")
        self.assertEqual(result.url, "https://example.org/original.jpg")
        self.assertEqual(result.width_hint, 1277)
        self.assertEqual(result.descriptor, "1277w")

    def test_density_srcset_is_supported_without_assuming_absolute_width(self):
        result = self.resolve("srcset='/one.png 1x, /two.png 2x'")
        self.assertEqual(result.url, "https://example.org/two.png")
        self.assertIsNone(result.width_hint)

    def test_src_is_used_when_srcset_is_absent(self):
        self.assertEqual(self.resolve("src='poster.png'").url,
                         "https://example.org/list/poster.png")

    def test_explicit_src_preference_bypasses_srcset(self):
        cfg = {**self.config, "prefer": "src"}
        result = self.resolve("src='/wanted.png' srcset='invalid value'", cfg)
        self.assertEqual(result.url, "https://example.org/wanted.png")

    def test_custom_lazyload_attributes_are_supported(self):
        cfg = {**self.config, "attribute": "data-src", "srcset_attribute": "data-srcset"}
        result = self.resolve("data-src='/thumb.png' data-srcset='/original.png 1200w'", cfg)
        self.assertEqual(result.url, "https://example.org/original.png")

    def test_base_element_relative_query_and_fragment_are_handled(self):
        result = self.resolve("src='poster.jpg?size=full#photo'",
                              prefix="<base href='/assets/'>")
        self.assertEqual(result.url, "https://example.org/assets/poster.jpg?size=full")

    def test_missing_and_multiple_elements_fail_instead_of_selecting_a_logo(self):
        for content in ("<html><img src='/logo.png'></html>",
                        "<html><img id='poster'><img id='poster'></html>"):
            with self.subTest(content=content):
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    resolve_image_from_html(content, self.url, self.config)

    def test_xpath_must_select_an_image_element(self):
        for selector in ("//img/@src", "string(//img/@src)", "//body"):
            with self.subTest(selector=selector):
                with self.assertRaises(ValueError):
                    self.resolve("src='/x.jpg'", {"selector": selector})

    def test_non_http_or_credential_urls_are_rejected(self):
        for url in ("javascript:alert(1)", "data:image/png;base64,AAAA", "file:///tmp/img.png",
                    "https://user:password@example.org/image.jpg", ""):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    self.resolve(f"src='{url}'")

    def test_malformed_mixed_or_duplicate_descriptors_are_rejected(self):
        for value in ("/a.jpg", "/a.jpg 0w", "/a.jpg -1w", "/a.jpg 1.2w",
                      "/a.jpg 100w, /b.jpg 2x", "/a.jpg 100w, /b.jpg 100w",
                      "/a.jpg 100w,", "/a,b.jpg 100w"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.resolve(f"srcset='{value}'")

    def test_css_selector_is_supported_when_available(self):
        try:
            import cssselect  # noqa: F401
        except ImportError:
            self.skipTest("cssselect belongs to the main Scrapy environment")
        result = self.resolve("src='/x.jpg'", {"selector_type": "css", "selector": "img#poster"})
        self.assertEqual(result.url, "https://example.org/x.jpg")

    def test_pdea_snapshot_selects_original_not_the_300px_src_or_header(self):
        root = Path(__file__).resolve().parents[3]
        config = deepcopy(WATCHLIST_CONFIGS["PDEA_MOST_WANTED"])
        image_config = config['bypass_config']['actions'][-1]['image']
        page = (root / 'test/fixtures/watchlist/pdea_most_wanted/source_page.html').read_text(encoding='utf-8')
        result = resolve_image_from_html(page, config['url'], image_config)
        self.assertEqual(result.url, 'https://pdea.gov.ph/wp-content/uploads/2023/06/mostwanted.jpg')
        self.assertEqual(result.width_hint, 1277)


if __name__ == '__main__':
    unittest.main()
