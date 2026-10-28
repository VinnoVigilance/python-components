"""Self-contained HTML capture using real PNGs and a mocked browser transport."""

import base64
import hashlib
import io
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

from lxml import html
from PIL import Image, PngImagePlugin

from ingestion.bypassCollector import BypassCollector
from ingestion.bypassCollector.actions.saveAction import SaveHtmlAction


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "test/fixtures/watchlist/pnp_sdsppo_most_wanted"
PAGE_URL = "https://sdsppo.pro13.pnp.gov.ph/?page_id=288"
ASSET_ROOT = "https://sdsppo.pro13.pnp.gov.ph/wp-content/uploads/2022/10/"


class TestSaveHtmlEmbeddedImages(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()
        self.markup = (FIXTURE / "source_page.html").read_text(encoding="utf-8")
        self.payloads = {ASSET_ROOT + f"{i}-1.png": (FIXTURE / f"sdsppo_{i:02d}.png").read_bytes()
                         for i in range(1, 5)}
        self.loaded = []
        for url, payload in self.payloads.items():
            with Image.open(io.BytesIO(payload)) as image:
                self.loaded.append({"url": url, "width": image.width, "height": image.height})
        self.context = {"source_name": "PNP", "list_name": "PNP_SDSPPO_MOST_WANTED",
                        "url": PAGE_URL, "outputDir": self.folder}
        self.config = {"action": "save_html", "embed_images": {
            "content_selector": "article#post-288 .entry-content",
            "expected_count": 4,
        }}
        self.engine = MagicMock()
        self.engine.getHtml.return_value = self.markup
        self.engine.getCurrentUrl.return_value = PAGE_URL
        self.engine.evaluateAwait.return_value = self.loaded
        self.fetcher = patch(
            "ingestion.bypassCollector.browserBinaryDownload.fetch_browser_binary",
            side_effect=self.fetch,
        ).start()
        self.addCleanup(patch.stopall)

    def fetch(self, engine, url, **kwargs):
        return self.payloads[url], {"final_url": url, "content_type": "image/png"}

    def capture(self):
        self.assertTrue(SaveHtmlAction().execute(self.config, self.context, self.engine))
        path = Path(self.context["savedFile"])
        return path, path.read_bytes()

    def assert_failure(self):
        self.context["savedFile"] = "stale-file.html"
        with self.assertLogs("ingestion.bypassCollector.actions.saveAction", level="ERROR"):
            self.assertFalse(SaveHtmlAction().execute(self.config, self.context, self.engine))
        self.assertNotIn("savedFile", self.context)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_all_original_png_bytes_are_embedded_in_a_single_html_file(self):
        path, data = self.capture()
        root = html.fromstring(data)
        figures = root.xpath("//main[@data-ocr-images='1']/figure")
        self.assertEqual(len(figures), 4)
        self.assertEqual(root.xpath("//meta[@name='snapshot-format']/@content"),
                         ["embedded-images-v1"])
        self.assertEqual(root.xpath("//meta[@name='source-url']/@content"), [PAGE_URL])
        for figure in figures:
            node = figure.xpath("./img")[0]
            prefix, encoded = node.get("src").split(",", 1)
            self.assertEqual(prefix, "data:image/png;base64")
            self.assertEqual(base64.b64decode(encoded, validate=True),
                             self.payloads[figure.get("data-source-url")])
            self.assertNotIn("srcset", node.attrib)
        self.assertFalse(root.xpath("//script | //iframe | //link"))
        self.assertEqual([p.name for p in self.folder.iterdir()], [path.name])

    def test_repeated_captures_have_unique_filenames_and_identical_hashes(self):
        first, a = self.capture()
        second, b = self.capture()
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(a, b)
        self.assertEqual(hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest())
        self.assertNotIn(first.name.encode(), a)
        self.assertNotIn(str(self.folder).encode(), a)

    def test_unrelated_dom_scripts_and_capture_attributes_do_not_change_the_hash(self):
        _, first = self.capture()
        self.engine.getHtml.return_value = self.markup.replace(
            "<head>", "<head><script>window.challengeToken='changed';</script>"
        ).replace("<body>", '<body data-capture-time="tomorrow"><aside>Unrelated widget</aside>')
        _, second = self.capture()
        self.assertEqual(first, second)

    def test_image_bytes_change_is_detected_when_the_asset_url_stays_the_same(self):
        _, first = self.capture()
        url = self.loaded[0]["url"]
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("source_revision", "changed")
        with Image.open(io.BytesIO(self.payloads[url])) as image:
            stream = io.BytesIO()
            image.save(stream, "PNG", pnginfo=metadata)
        self.payloads[url] = stream.getvalue()
        _, second = self.capture()
        self.assertNotEqual(hashlib.sha256(first).digest(), hashlib.sha256(second).digest())

    def test_meaningful_text_in_the_content_section_is_kept_and_hashed(self):
        _, first = self.capture()
        root = html.fromstring(self.markup)
        node = html.Element("p")
        node.text = "Corrected source information"
        root.cssselect(self.config["embed_images"]["content_selector"])[0].append(node)
        self.engine.getHtml.return_value = html.tostring(root, encoding="unicode")
        _, second = self.capture()
        self.assertNotEqual(first, second)
        self.assertIn(b"Corrected source information", second)

    def test_browser_dom_order_does_not_change_canonical_image_order(self):
        _, first = self.capture()
        root = html.fromstring(self.markup)
        nodes = root.xpath("//img")
        replacements = [deepcopy(node) for node in reversed(nodes)]
        for node, replacement in zip(nodes, replacements):
            node.getparent().replace(node, replacement)
        self.engine.getHtml.return_value = html.tostring(root, encoding="unicode")
        self.engine.evaluateAwait.return_value = list(reversed(self.loaded))
        _, second = self.capture()
        self.assertEqual(first, second)

    def test_transport_uses_largest_https_candidates_and_current_session_cache(self):
        self.capture()
        self.assertEqual([call.args[1] for call in self.fetcher.call_args_list], list(self.payloads))
        for call in self.fetcher.call_args_list:
            self.assertEqual(call.kwargs["cache_mode"], "force-cache")
            self.assertEqual(call.kwargs["page_url"], PAGE_URL)

    def test_one_failed_image_prevents_any_partial_snapshot(self):
        def fail_last(engine, url, **kwargs):
            if url.endswith("4-1.png"):
                raise RuntimeError("Asset HTTP 403")
            return self.fetch(engine, url, **kwargs)
        self.fetcher.side_effect = fail_last
        self.assert_failure()
        self.assertEqual(self.fetcher.call_count, 4)

    def test_wrong_image_count_is_rejected_before_browser_loading_or_downloading(self):
        self.config["embed_images"]["expected_count"] = 3
        self.assert_failure()
        self.engine.evaluateAwait.assert_not_called()
        self.fetcher.assert_not_called()

    def test_incomplete_lazy_loading_cannot_be_a_successful_capture(self):
        self.engine.evaluateAwait.return_value = None
        self.assert_failure()
        self.fetcher.assert_not_called()

    def test_thumbnail_loaded_instead_of_selected_original_is_rejected(self):
        loaded = deepcopy(self.loaded)
        loaded[0]["url"] = ASSET_ROOT + "1-1-240x300.png"
        self.engine.evaluateAwait.return_value = loaded
        self.assert_failure()
        self.fetcher.assert_not_called()

    def test_error_html_cannot_be_embedded_as_an_image(self):
        self.fetcher.side_effect = None
        self.fetcher.return_value = (b"<html>403 Forbidden</html>", {})
        self.assert_failure()

    def test_image_dimensions_must_match_the_browser_resource(self):
        loaded = deepcopy(self.loaded)
        loaded[0]["width"] += 1
        self.engine.evaluateAwait.return_value = loaded
        self.assert_failure()

    def test_duplicate_image_urls_are_rejected_instead_of_duplicate_records(self):
        root = html.fromstring(self.markup)
        nodes = root.xpath("//img")
        nodes[-1].set("srcset", nodes[0].get("srcset"))
        self.engine.getHtml.return_value = html.tostring(root, encoding="unicode")
        self.assert_failure()
        self.engine.evaluateAwait.assert_not_called()

    def test_missing_or_ambiguous_content_section_is_rejected(self):
        for selector in ("article#missing", "img"):
            with self.subTest(selector=selector):
                self.config["embed_images"]["content_selector"] = selector
                self.assert_failure()
        self.fetcher.assert_not_called()

    def test_failed_atomic_publish_leaves_no_partial_file_or_context(self):
        with patch.object(Path, "replace", side_effect=OSError("Cannot publish snapshot")):
            self.assert_failure()

    def test_source_selector_and_expected_count_are_required_and_validated(self):
        for settings in (None, {}, {"content_selector": "article", "expected_count": True},
                         {"content_selector": "article", "expected_count": 0},
                         {"content_selector": "article", "expected_count": 4, "typo": True},
                         {"content_selector": "article", "expected_count": 4, "timeout_seconds": False}):
            with self.subTest(settings=settings):
                self.config["embed_images"] = settings
                self.assert_failure()
        self.fetcher.assert_not_called()

    def test_plain_save_html_retains_the_existing_dom_and_does_not_fetch_assets(self):
        self.assertTrue(SaveHtmlAction().execute({}, self.context, self.engine))
        self.assertEqual(Path(self.context["savedFile"]).read_text(encoding="utf-8"), self.markup)
        self.engine.evaluateAwait.assert_not_called()
        self.fetcher.assert_not_called()

    def test_existing_collector_and_action_executor_return_the_html_primary_file(self):
        config = {"source_name": "PNP", "list_name": "PNP_SDSPPO_MOST_WANTED",
                  "url": PAGE_URL, "file_type": "html", "bypass_config": {
                      "challenge": "cloudflare", "navigation_mode": "safe_get",
                      "actions": [self.config], "validation": {"min_size_bytes": 1000},
                  }}
        self.engine.__enter__.return_value = self.engine
        self.engine.navigate.return_value = True
        collector = BypassCollector(outputDir=self.folder)
        with patch.object(collector, "_createEngine", return_value=self.engine), patch.object(
            collector, "_cleanupBrowserArtifacts"
        ):
            path = collector.collect(config)
        self.assertIsNotNone(path)
        self.assertEqual(path.suffix, ".html")
        self.assertEqual(len(html.fromstring(path.read_bytes()).xpath("//figure/img")), 4)
        self.engine.navigate.assert_called_once_with(PAGE_URL)


if __name__ == "__main__":
    unittest.main()
