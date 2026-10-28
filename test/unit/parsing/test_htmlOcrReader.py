"""Read real embedded PNGs with saved OCR lines; no model/network/DB access."""

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lxml import html
from PIL import UnidentifiedImageError

from parsing.ocr.documentOcrReader import DocumentOcrReader, DocumentOcrSettings
from parsing.ocr.rapidOcrEngine import OcrSettings, RapidOcrEngine
from parsing.parserFactory import create_parser


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "test/fixtures/watchlist/pnp_sdsppo_most_wanted"


class TestHtmlOcrReader(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name).resolve() / "source.html"
        self.source.write_bytes((FIXTURE / "selfcontained_source.html").read_bytes())
        self.reference = json.loads((FIXTURE / "ocr_pages.json").read_text(encoding="utf-8"))["pages"]
        self.by_size = {tuple(page["image_size"]): page for page in self.reference}
        self.backend = Mock(side_effect=self.infer)
        self.builder = patch.object(RapidOcrEngine, "_build_backend", return_value=self.backend).start()
        self.addCleanup(patch.stopall)

    def infer(self, image, **kwargs):
        lines = self.by_size[image.size]["lines"]
        return SimpleNamespace(txts=[line["text"] for line in lines],
                               scores=[line["score"] for line in lines],
                               boxes=[line["box"] for line in lines])

    def change(self, callback):
        root = html.fromstring(self.source.read_bytes())
        callback(root)
        self.source.write_bytes(html.tostring(root, encoding="utf-8"))

    def layout(self):
        return {"schema_version": 1, "layout_id": "HTML_SMOKE_TEST", "layout_version": 1,
                "expected_page_count": 4,
                "fields": {"page_text": {"box": [0, 0, 1, 1], "mode": "join", "required": True}},
                "pages": [{"page_number": page["page_number"], "reference_size": page["image_size"],
                           "record_regions": [{"key": "whole_page", "box": [0, 0, *page["image_size"]]}]}
                          for page in self.reference]}

    def test_all_four_images_are_read_at_native_size_and_bound_to_the_html_hash(self):
        result = DocumentOcrReader().read(self.source)
        self.assertEqual(result["document_type"], "html")
        self.assertEqual(result["document_page_count"], 4)
        self.assertEqual(result["selected_pages"], [1, 2, 3, 4])
        self.assertTrue(result["all_pages_processed"])
        self.assertEqual(result["source_sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(result["source_size_bytes"], self.source.stat().st_size)
        self.builder.assert_called_once()
        for index, page in enumerate(result["pages"], 1):
            self.assertEqual(page["image_size"], self.reference[index - 1]["image_size"])
            self.assertEqual(page["pixel_sha256"], self.reference[index - 1]["pixel_sha256"])
            original = (FIXTURE / f"sdsppo_{index:02d}.png").read_bytes()
            rendering = page["rendering"]
            self.assertEqual(rendering["method"], "embedded_html_image")
            self.assertEqual(rendering["source_image_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(rendering["source_image_size_bytes"], len(original))

    def test_all_419_saved_lines_scores_and_coordinates_are_preserved(self):
        result = DocumentOcrReader().read(self.source)
        self.assertEqual(sum(page["line_count"] for page in result["pages"]), 419)
        for actual, expected in zip(result["pages"], self.reference):
            self.assertEqual(actual["lines"], expected["lines"])

    def test_image_urls_are_provenance_only_and_no_network_is_used_by_the_reader(self):
        with patch("socket.socket", side_effect=AssertionError("No network in the image reader")):
            result = DocumentOcrReader().read(self.source)
        self.assertTrue(result["pages"][0]["rendering"]["source_image_url"].endswith("1-1.png"))

    def test_explicit_page_selection_preserves_original_indexes_and_order(self):
        result = DocumentOcrReader().read(self.source, page_numbers=[4, 2])
        self.assertEqual(result["document_page_count"], 4)
        self.assertEqual(result["selected_pages"], [4, 2])
        self.assertFalse(result["all_pages_processed"])
        self.assertEqual([page["page_number"] for page in result["pages"]], [4, 2])
        self.assertEqual([page["image_size"] for page in result["pages"]],
                         [self.reference[3]["image_size"], self.reference[1]["image_size"]])

    def test_normal_html_and_challenge_pages_are_rejected_before_inference(self):
        self.source.write_text("<html><body>Just a moment...</body></html>", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "embedded-images-v1"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_unknown_snapshot_version_is_rejected(self):
        self.change(lambda root: root.xpath("//meta[@name='snapshot-format']")[0].set("content", "unknown"))
        with self.assertRaisesRegex(ValueError, "snapshot"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_external_image_urls_are_rejected_without_being_downloaded(self):
        self.change(lambda root: root.xpath("//img")[0].set("src", "https://example.org/image.png"))
        with self.assertRaisesRegex(ValueError, "data URIs"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_active_content_is_not_accepted_as_a_snapshot(self):
        self.change(lambda root: root.xpath("//body")[0].append(html.Element("script")))
        with self.assertRaisesRegex(ValueError, "active/external"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_missing_container_is_rejected(self):
        self.change(lambda root: root.xpath("//main")[0].attrib.pop("data-ocr-images"))
        with self.assertRaisesRegex(ValueError, "container"):
            DocumentOcrReader().read(self.source)

    def test_missing_or_duplicate_image_indexes_are_rejected(self):
        self.change(lambda root: root.xpath("//figure")[0].set("data-image-index", "2"))
        with self.assertRaisesRegex(ValueError, "indexes"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_undeclared_extra_images_cannot_be_silently_ignored(self):
        self.change(lambda root: root.xpath("//body")[0].append(html.Element("img")))
        with self.assertRaisesRegex(ValueError, "one embedded image per figure"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_invalid_base64_is_not_an_ocr_result(self):
        self.change(lambda root: root.xpath("//img")[0].set("src", "data:image/png;base64,???"))
        with self.assertRaisesRegex(ValueError, "base64"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_mime_type_must_match_image_bytes(self):
        def wrong_mime(root):
            node = root.xpath("//img")[0]
            node.set("src", node.get("src").replace("image/png", "image/jpeg", 1))
        self.change(wrong_mime)
        with self.assertRaisesRegex(ValueError, "MIME"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_declared_dimensions_must_match_the_embedded_image(self):
        self.change(lambda root: root.xpath("//img")[0].set("width", "1"))
        with self.assertRaisesRegex(ValueError, "dimensions"):
            DocumentOcrReader().read(self.source)
        self.builder.assert_not_called()

    def test_late_corrupt_image_does_not_return_a_partial_document(self):
        self.change(lambda root: root.xpath("//img")[3].set(
            "src", "data:image/png;base64," + base64.b64encode(b"broken image").decode("ascii")
        ))
        with self.assertRaises(UnidentifiedImageError):
            DocumentOcrReader().read(self.source)
        self.assertEqual(self.backend.call_count, 3)

    def test_page_limit_is_not_a_silent_truncation(self):
        reader = DocumentOcrReader(settings=DocumentOcrSettings(max_pages=2))
        with self.assertRaisesRegex(ValueError, "max_pages"):
            reader.read(self.source)
        self.builder.assert_not_called()

    def test_pixel_limit_is_checked_before_inference(self):
        reader = DocumentOcrReader(RapidOcrEngine(OcrSettings(max_pixels=100)))
        with self.assertRaisesRegex(ValueError, "max_pixels"):
            reader.read(self.source)
        self.builder.assert_not_called()

    def test_source_byte_limit_applies_to_the_whole_html(self):
        reader = DocumentOcrReader(settings=DocumentOcrSettings(max_source_bytes=1000))
        with self.assertRaisesRegex(ValueError, "max_source_bytes"):
            reader.read(self.source)
        self.builder.assert_not_called()

    def test_existing_parser_interface_reads_html_images_with_a_layout(self):
        config = {"file_type": "html", "parser": {"mode": "ocr", "layout": self.layout()}}
        records = create_parser("html").parse(self.source, config)
        self.assertEqual(len(records), 4)  # One whole-page smoke record, not the person layout.
        self.assertEqual([record["_ocr"]["page_number"] for record in records], [1, 2, 3, 4])
        self.assertTrue(all(record["_ocr"]["source_sha256"] == hashlib.sha256(self.source.read_bytes()).hexdigest()
                            for record in records))
        self.assertTrue(all(record["_ocr"]["execution_mode"] == "in_process" for record in records))

    def test_html_parser_requires_explicit_ocr_mode_before_inference(self):
        config = {"file_type": "html", "parser": {"layout": self.layout()}}
        with self.assertRaisesRegex(ValueError, "explicit parser.mode"):
            create_parser("html").parse(self.source, config)
        self.builder.assert_not_called()

    def test_replay_is_bound_to_the_html_instead_of_an_individual_png(self):
        document = DocumentOcrReader().read(self.source)
        cache = self.source.with_suffix(".json")
        cache.write_text(json.dumps(document), encoding="utf-8")
        config = {"file_type": "html", "parser": {"mode": "ocr", "layout": self.layout(),
                                                   "saved_ocr_path": str(cache)}}
        self.builder.reset_mock()
        records = create_parser("html").parse(self.source, config)
        self.builder.assert_not_called()
        self.assertEqual(len(records), 4)
        self.source.write_bytes(self.source.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "source hash"):
            create_parser("html").parse(self.source, config)


if __name__ == "__main__":
    unittest.main()
