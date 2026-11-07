"""Recognition resize preserves source bytes and the layout coordinate space."""

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pypdfium2 as pdfium
from PIL import Image

from parsing.ocr.documentOcrReader import DocumentOcrReader, DocumentOcrSettings
from parsing.ocr.rapidOcrEngine import OcrSettings, RapidOcrEngine
from parsing.ocrParser import OcrParser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "test/fixtures/watchlist/pnp_sdsppo_most_wanted"


class TestDocumentOcrScale(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "source.png"
        with Image.new("RGB", (20, 10), "white") as image:
            image.save(self.source)
        self.backend = Mock(side_effect=self.infer)
        builder = patch.object(RapidOcrEngine, "_build_backend", return_value=self.backend)
        self.builder = builder.start()
        self.addCleanup(builder.stop)

    @staticmethod
    def infer(image, **kwargs):
        w, h = image.size
        return SimpleNamespace(txts=["EXAMPLE"], scores=[0.99],
                               boxes=[[[w / 10, h / 5], [w * .9, h / 5],
                                       [w * .9, h * .8], [w / 10, h * .8]]])

    def read(self, scale=1, source=None):
        return DocumentOcrReader(settings=DocumentOcrSettings(
            recognition_scale=scale)).read(source or self.source)

    def replay_config(self, document, scale=2):
        saved = self.root / "ocr.json"
        saved.write_text(json.dumps(document), encoding="utf-8")
        return {"file_type": "image", "parser": {
            "mode": "ocr", "saved_ocr_path": str(saved),
            "document": {"recognition_scale": scale},
            "layout": {"schema_version": 1, "layout_id": "SCALE", "layout_version": 1,
                       "fields": {"name": {"box": [0, 0, 1, 1], "required": True}},
                       "pages": [{"page_number": 1, "reference_size": [20, 10],
                                  "record_regions": [{"key": "row", "box": [0, 0, 20, 10]}]}]},
        }}

    def test_default_is_one_and_existing_positional_settings_keep_meaning(self):
        settings = DocumentOcrSettings(144, 20, 1000)
        self.assertEqual((settings.pdf_render_dpi, settings.max_pages,
                          settings.max_source_bytes, settings.recognition_scale), (144, 20, 1000, 1))
        page = self.read()["pages"][0]
        self.assertEqual(page["processed_size"], [20, 10])
        self.assertEqual(page["rendering"]["recognition_scale"], 1)

    def test_double_size_preserves_source_hash_pixels_and_original_coordinates(self):
        original = self.source.read_bytes()
        native, scaled = self.read(), self.read(2)
        first, second = native["pages"][0], scaled["pages"][0]
        self.assertEqual(second["processed_size"], [40, 20])
        self.assertEqual(second["image_size"], [20, 10])
        self.assertEqual(second["scale_xy"], [2, 2])
        self.assertEqual(second["rendering"]["recognition_scale"], 2)
        self.assertEqual(first["lines"], second["lines"])
        self.assertEqual(first["pixel_sha256"], second["pixel_sha256"])
        self.assertEqual(native["source_sha256"], scaled["source_sha256"])
        self.assertEqual(scaled["source_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.source.read_bytes(), original)

    def test_html_images_are_resized_after_decoding_original_bytes(self):
        source = FIXTURE / "selfcontained_source.html"
        document = self.read(2, source)
        self.assertEqual(document["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(len(document["pages"]), 4)
        for index, page in enumerate(document["pages"], 1):
            w, h = page["image_size"]
            self.assertEqual(page["processed_size"], [w * 2, h * 2])
            self.assertEqual(page["rendering"]["recognition_scale"], 2)
            self.assertEqual(page["rendering"]["source_image_sha256"], hashlib.sha256(
                (FIXTURE / f"sdsppo_{index:02d}.png").read_bytes()).hexdigest())

    def test_pdf_render_dpi_and_recognition_scale_are_independent(self):
        source = self.root / "source.pdf"
        pdf = pdfium.PdfDocument.new()
        page = pdf.new_page(20, 10)
        page.close()
        pdf.save(source)
        pdf.close()
        result = DocumentOcrReader(settings=DocumentOcrSettings(
            pdf_render_dpi=72, recognition_scale=2)).read(source)["pages"][0]
        self.assertEqual(result["image_size"], [20, 10])
        self.assertEqual(result["processed_size"], [40, 20])
        self.assertEqual(result["rendering"]["requested_dpi"], 72)

    def test_invalid_scales_are_rejected(self):
        for scale in (0, -1, True, None, "2", float("inf"), float("nan")):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                DocumentOcrSettings(recognition_scale=scale)

    def test_scaled_pixel_limit_is_checked_before_model_loading(self):
        reader = DocumentOcrReader(RapidOcrEngine(OcrSettings(max_pixels=500)),
                                   DocumentOcrSettings(recognition_scale=2))
        with self.assertRaisesRegex(RuntimeError, "document page 1") as error:
            reader.read(self.source)
        self.assertIn("max_pixels", str(error.exception.__cause__))
        self.builder.assert_not_called()

    def test_parser_rejects_old_native_ocr_when_double_scale_is_requested(self):
        config = self.replay_config(self.read())
        with self.assertRaisesRegex(ValueError, "recognition scale differs"):
            OcrParser().parse(self.source, config)

    def test_matching_scale_replay_does_not_run_inference(self):
        config = self.replay_config(self.read(2))
        self.backend.reset_mock()
        records = OcrParser().parse(self.source, config)
        self.assertEqual(records[0]["name"], "EXAMPLE")
        self.assertEqual(records[0]["_ocr"]["rendering"]["recognition_scale"], 2)
        self.backend.assert_not_called()

    def test_legacy_replay_without_scale_metadata_still_works_at_default_scale(self):
        document = self.read()
        del document["pages"][0]["rendering"]["recognition_scale"]
        config = self.replay_config(document, scale=1)
        self.assertEqual(OcrParser().parse(self.source, config)[0]["name"], "EXAMPLE")

    def test_replay_cannot_bypass_scaled_pixel_limit(self):
        config = self.replay_config(self.read(2))
        config["parser"]["engine"] = {"max_pixels": 500}
        with self.assertRaisesRegex(ValueError, "Scaled OCR page"):
            OcrParser().parse(self.source, config)

    def test_real_source_config_keeps_eighteen_layout_regions_at_double_scale(self):
        reference = json.loads((FIXTURE / "ocr_pages.json").read_text(encoding="utf-8"))["pages"]
        by_size = {(page["image_size"][0] * 2, page["image_size"][1] * 2): page
                   for page in reference}

        def infer(image, **kwargs):
            lines = by_size[image.size]["lines"]
            return SimpleNamespace(txts=[line["text"] for line in lines],
                                   scores=[line["score"] for line in lines],
                                   boxes=[[[x * 2, y * 2] for x, y in line["box"]] for line in lines])

        self.backend.side_effect = infer
        config = deepcopy(WATCHLIST_CONFIGS["PNP_SDSPPO_MOST_WANTED"])
        records = OcrParser().parse(FIXTURE / "selfcontained_source.html", config)
        self.assertEqual(len(records), 18)
        self.assertEqual(self.backend.call_count, 4)
        for row in records:
            self.assertEqual(row["_ocr"]["rendering"]["recognition_scale"], 2)
            page = reference[row["_ocr"]["page_number"] - 1]
            self.assertEqual(row["_ocr"]["image_size"], page["image_size"])
        self.assertEqual(WATCHLIST_CONFIGS["PDEA_MOST_WANTED"]["parser"].get("document", {}).get("recognition_scale", 1), 1)


if __name__ == "__main__":
    unittest.main()
