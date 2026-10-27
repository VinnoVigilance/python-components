"""Parser-factory integration, source-bound replay and legacy PDF routing."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from parsing.ocr.rapidOcrEngine import OcrSettings
from parsing.ocrParser import OcrParser
from parsing.parserFactory import create_parser
from parsing.pdfParser import PdfParser


class TestParserOcrIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Windows TEMP may use an 8.3 alias; match the parser's canonical paths.
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source.png"
        with Image.new("RGB", (20, 10), "white") as image:
            image.save(self.source)
        data = self.source.read_bytes()
        self.layout = {
            "schema_version": 1, "layout_id": "EXAMPLE", "layout_version": 1,
            "fields": {"name": {"box": [0, 0, 1, 1], "required": True}},
            "pages": [{"page_number": 1, "reference_size": [20, 10],
                       "record_regions": [{"key": "region", "box": [0, 0, 20, 10]}]}],
        }
        self.document = {
            "schema_version": 1, "source_path": "original/location/source.png",
            "source_sha256": hashlib.sha256(data).hexdigest(), "source_size_bytes": len(data),
            "document_type": "image", "document_page_count": 1,
            "selected_pages": [1], "processed_page_count": 1, "all_pages_processed": True,
            "pages": [{"schema_version": 1, "page_number": 1, "image_size": [20, 10],
                       "coordinate_space": "upright_image_pixels", "region": [0, 0, 20, 10],
                       "status": "TEXT_FOUND", "line_count": 1,
                       "settings": asdict(OcrSettings()),
                       "lines": [{"line_number": 1, "text": "EXAMPLE NAME", "score": 0.98,
                                  "box": [[1, 1], [19, 1], [19, 9], [1, 9]]}]}],
        }
        self.layout_path = self.root / "layout.json"
        self.cache = self.root / "ocr.json"
        self.layout_path.write_text(json.dumps(self.layout), encoding="utf-8")
        self.save_cache()
        self.config = {"file_type": "image", "parser": {
            "mode": "ocr", "layout_path": str(self.layout_path),
            "saved_ocr_path": str(self.cache),
        }}

    def save_cache(self):
        self.cache.write_text(json.dumps(self.document), encoding="utf-8")

    def parse(self):
        return create_parser(self.config["file_type"]).parse(self.source, self.config)

    def test_factory_registers_image_formats_without_changing_legacy_routes(self):
        for name in ("image", "img", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp", "gif"):
            with self.subTest(name=name):
                self.assertIsInstance(create_parser(name), OcrParser)
        self.assertIsInstance(create_parser("  IMAGE  "), OcrParser)
        self.assertIsInstance(create_parser("pdf"), PdfParser)
        self.assertEqual(create_parser("xml").__class__.__name__, "XmlParser")
        self.assertEqual(create_parser("json").__class__.__name__, "JsonParser")
        self.assertEqual(create_parser("csv").__class__.__name__, "TabularParser")

    def test_factory_construction_does_not_import_recognition_runtime(self):
        code = (
            "import sys; sys.modules['rapidocr']=None; sys.modules['onnxruntime']=None; "
            "from parsing.parserFactory import create_parser; "
            "create_parser('image'); create_parser('xml'); create_parser('pdf')"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_source_bound_replay_does_not_run_ocr(self):
        with patch("parsing.ocr.rapidOcrEngine.RapidOcrEngine._build_backend",
                   side_effect=AssertionError("No inference in replay")):
            result = self.parse()
        self.assertEqual(result[0]["name"], "EXAMPLE NAME")
        self.assertEqual(result[0]["_ocr"]["recognition_mode"], "replay")
        self.assertEqual(result[0]["_ocr"]["source_path"], str(self.source))
        self.assertEqual(result[0]["_ocr"]["ocr_document_source_path"],
                         "original/location/source.png")

    def test_different_input_bytes_cannot_reuse_saved_ocr(self):
        self.source.write_bytes(b"changed source")
        with self.assertRaisesRegex(ValueError, "source hash"):
            self.parse()

    def test_replay_source_size_must_also_match(self):
        self.document["source_size_bytes"] += 1
        self.save_cache()
        with self.assertRaisesRegex(ValueError, "source size"):
            self.parse()

    def test_replay_page_selection_must_match_explicit_settings(self):
        self.config["parser"]["page_numbers"] = [2]
        with self.assertRaisesRegex(ValueError, "selected pages"):
            self.parse()

    def test_explicit_model_override_cannot_silently_use_another_model(self):
        self.config["parser"]["engine"] = {"recognition_language": "CH"}
        with self.assertRaisesRegex(ValueError, "engine setting differs"):
            self.parse()

    def test_normalized_matching_engine_override_is_allowed(self):
        self.config["parser"]["engine"] = {"recognition_language": " en "}
        self.assertEqual(self.parse()[0]["name"], "EXAMPLE NAME")

    def test_source_size_limit_applies_to_replay(self):
        self.config["parser"]["document"] = {"max_source_bytes": 1}
        with self.assertRaisesRegex(ValueError, "max_source_bytes"):
            self.parse()

    def test_page_pixel_limit_applies_to_replay(self):
        self.config["parser"]["engine"] = {"max_pixels": 100}
        with self.assertRaisesRegex(ValueError, "max_pixels"):
            self.parse()

    def test_declared_type_cannot_silently_accept_other_content(self):
        self.config["file_type"] = "pdf"
        with self.assertRaisesRegex(ValueError, "Expected pdf content"):
            self.parse()

    def test_relative_layout_and_replay_paths_use_project_root(self):
        self.config["parser"]["layout_path"] = "layout.json"
        self.config["parser"]["saved_ocr_path"] = "ocr.json"
        with patch("parsing.ocrParser.ROOT_DIR", self.root):
            self.assertEqual(self.parse()[0]["name"], "EXAMPLE NAME")

    def test_inline_layout_is_supported_without_mutating_configuration(self):
        self.config["parser"].pop("layout_path")
        self.config["parser"]["layout"] = self.layout
        original = deepcopy(self.config)
        self.assertEqual(self.parse()[0]["name"], "EXAMPLE NAME")
        self.assertEqual(self.config, original)

    def test_fresh_mode_calls_document_reader_with_settings_and_pages(self):
        self.config["parser"].pop("saved_ocr_path")
        self.config["parser"]["page_numbers"] = [1]
        with patch("parsing.ocr.documentOcrReader.DocumentOcrReader.read",
                   return_value=deepcopy(self.document)) as read:
            result = self.parse()
        read.assert_called_once_with(self.source, page_numbers=[1])
        self.assertEqual(result[0]["_ocr"]["recognition_mode"], "fresh")
        self.assertNotIn("saved_ocr_path", result[0]["_ocr"])

    def test_missing_or_invalid_parser_settings_fail_explicitly(self):
        for cfg in ({}, {"parser": {}}, {"parser": {"mode": "text", "layout": self.layout}},
                    {"parser": {"layout": self.layout, "layout_path": str(self.layout_path)}},
                    {"parser": {"layout": self.layout, "typo": True}}):
            with self.subTest(cfg=cfg):
                with self.assertRaises(ValueError):
                    OcrParser().parse(self.source, cfg)

    def test_unknown_engine_and_document_options_fail_before_inference(self):
        for field in ("engine", "document"):
            cfg = deepcopy(self.config)
            cfg["parser"][field] = {"unknown_setting": 1}
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "Unknown OCR"):
                    OcrParser().parse(self.source, cfg)

    def test_invalid_page_numbers_fail_in_replay_too(self):
        for requested in ([], [True], [1, 1], "1", [0]):
            cfg = deepcopy(self.config)
            cfg["parser"]["page_numbers"] = requested
            with self.subTest(requested=requested):
                with self.assertRaises(ValueError):
                    OcrParser().parse(self.source, cfg)

    def test_missing_source_or_layout_is_not_an_empty_success(self):
        with self.assertRaises(FileNotFoundError):
            OcrParser().parse(self.root / "missing.png", self.config)
        self.config["parser"]["layout_path"] = str(self.root / "missing.json")
        with self.assertRaises(FileNotFoundError):
            self.parse()

    def test_pdf_ocr_mode_delegates_to_shared_parser_only_when_requested(self):
        cfg = {"parser": {"type": "pdf", "mode": " OCR ", "layout": self.layout}}
        with patch.object(OcrParser, "parse", return_value=[{"name": "result"}]) as parse:
            self.assertEqual(PdfParser().parse(self.source, cfg), [{"name": "result"}])
        parse.assert_called_once_with(file_path=self.source, config=cfg)

    def test_existing_pdf_table_mode_keeps_native_behavior(self):
        page = SimpleNamespace(extract_tables=lambda: [[['ID', 'NAME'], ['1', 'EXAMPLE']]])
        with patch("parsing.pdfParser.pdfplumber.open") as native:
            native.return_value.__enter__.return_value.pages = [page]
            with patch.object(OcrParser, "parse", side_effect=AssertionError("No OCR")):
                result = PdfParser().parse(self.source, {})
        self.assertEqual(result, [{"ID": "1", "NAME": "EXAMPLE"}])

    def test_existing_pdf_text_mode_keeps_native_behavior(self):
        with patch.object(PdfParser, "_page_texts", return_value=["first", "second"]):
            with patch.object(OcrParser, "parse", side_effect=AssertionError("No OCR")):
                result = PdfParser().parse(self.source, {"parser": {"mode": "text"}})
        self.assertEqual(result, [{"BodyText": "first\n\nsecond"}])





if __name__ == "__main__":
    unittest.main()
