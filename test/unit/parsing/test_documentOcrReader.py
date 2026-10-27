"""Real image/PDF decoding and mocked OCR; no recognition-model downloads."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError
from PIL.PngImagePlugin import PngInfo

from parsing.ocr.documentOcrReader import DocumentOcrReader, DocumentOcrSettings
from parsing.ocr.rapidOcrEngine import OcrSettings, RapidOcrEngine


class TestDocumentOcrReader(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.observed = []

        def infer(image, **kwargs):
            self.observed.append((image.size, image.mode, image.getpixel((0, 0))))
            return SimpleNamespace(
                txts=("SAMPLE TEXT",), scores=(0.9,),
                boxes=(((0, 0), (10, 0), (10, 5), (0, 5)),),
            )

        self.backend = Mock(side_effect=infer)
        self.builder = patch.object(
            RapidOcrEngine, "_build_backend", return_value=self.backend
        ).start()
        self.addCleanup(patch.stopall)
        self.engine = RapidOcrEngine()
        self.reader = DocumentOcrReader(self.engine)

    def image_file(self, name="source.png", *, frames=1):
        images = [Image.new("RGB", (40, 30), color)
                  for color in ("red", "blue", "green")[:frames]]
        path = self.root / name
        try:
            if frames > 1:
                images[0].save(path, format="TIFF", save_all=True, append_images=images[1:])
            else:
                images[0].save(path, format="PNG")
        finally:
            for image in images:
                image.close()
        return path

    def pdf_file(self, name="source.pdf", *, sizes=((72, 36),), rotation=0):
        path = self.root / name
        document = pdfium.PdfDocument.new()
        try:
            for width, height in sizes:
                page = document.new_page(width, height)
                try:
                    page.set_rotation(rotation)
                finally:
                    page.close()
            document.save(path)
        finally:
            document.close()
        return path

    def test_image_preserves_native_resolution_and_source_fingerprint(self):
        path = self.image_file()
        source = path.read_bytes()
        result = self.reader.read(path)
        self.assertEqual(result["document_type"], "image")
        self.assertEqual(result["image_format"], "PNG")
        self.assertEqual(result["document_page_count"], 1)
        self.assertEqual(result["source_sha256"], hashlib.sha256(source).hexdigest())
        self.assertEqual(result["source_size_bytes"], len(source))
        self.assertEqual(result["source_path"], str(path.resolve()))
        self.assertEqual(result["pages"][0]["image_size"], [40, 30])
        self.assertEqual(result["pages"][0]["rendering"]["method"], "native_image")
        self.assertEqual(path.read_bytes(), source)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    def test_file_type_is_detected_from_content_instead_of_suffix(self):
        image = self.image_file("image.pdf")
        self.assertEqual(self.reader.read(image)["document_type"], "image")
        pdf = self.pdf_file("document.bin")
        self.assertEqual(self.reader.read(pdf)["document_type"], "pdf")

    def test_pdf_marker_inside_image_metadata_does_not_change_document_type(self):
        path = self.root / "metadata.png"
        metadata = PngInfo()
        metadata.add_text("description", "%PDF-1.7 example")
        with Image.new("RGB", (40, 30), "white") as image:
            image.save(path, pnginfo=metadata)
        self.assertIn(b"%PDF-", path.read_bytes()[:1024])
        self.assertEqual(self.reader.read(path)["document_type"], "image")

    def test_image_file_exif_orientation_is_preserved_for_the_engine(self):
        path = self.root / "rotated.jpg"
        with Image.new("RGB", (40, 30), "white") as image:
            exif = image.getexif()
            exif[274] = 6
            image.save(path, exif=exif)
        result = self.reader.read(path)
        self.assertEqual(result["pages"][0]["image_size"], [30, 40])

    def test_all_tiff_frames_are_decoded_as_separate_numbered_pages(self):
        path = self.image_file("multipage.tiff", frames=3)
        result = self.reader.read(path)
        self.assertEqual(result["document_page_count"], 3)
        self.assertEqual(result["selected_pages"], [1, 2, 3])
        self.assertEqual(result["processed_page_count"], 3)
        self.assertTrue(result["all_pages_processed"])
        self.assertEqual([p["page_number"] for p in result["pages"]], [1, 2, 3])
        self.assertEqual([x[2] for x in self.observed],
                         [(255, 0, 0), (0, 0, 255), (0, 128, 0)])
        self.builder.assert_called_once()

    def test_requested_image_frame_order_is_preserved(self):
        path = self.image_file("multipage.tiff", frames=3)
        result = self.reader.read(path, page_numbers=[3, 1])
        self.assertEqual(result["selected_pages"], [3, 1])
        self.assertEqual([p["page_number"] for p in result["pages"]], [3, 1])
        self.assertFalse(result["all_pages_processed"])
        self.assertEqual([x[2] for x in self.observed], [(0, 128, 0), (255, 0, 0)])

    def test_real_pdf_pages_are_rendered_at_configured_scale(self):
        path = self.pdf_file(sizes=((72, 36), (36, 72)))
        reader = DocumentOcrReader(self.engine, DocumentOcrSettings(pdf_render_dpi=144))
        result = reader.read(path)
        self.assertEqual(result["document_type"], "pdf")
        self.assertEqual(result["document_page_count"], 2)
        self.assertTrue(result["all_pages_processed"])
        self.assertEqual([p["image_size"] for p in result["pages"]], [[144, 72], [72, 144]])
        self.assertEqual([p["page_number"] for p in result["pages"]], [1, 2])
        rendering = result["pages"][0]["rendering"]
        self.assertEqual(rendering["renderer"], "pypdfium2")
        self.assertEqual(rendering["requested_dpi"], 144)
        self.assertEqual(rendering["render_scale"], 2)
        self.assertEqual(rendering["page_size_canvas_units"], [72, 36])

    def test_pdf_rotation_is_honored_once(self):
        path = self.pdf_file(rotation=90)
        reader = DocumentOcrReader(self.engine, DocumentOcrSettings(pdf_render_dpi=72))
        page = reader.read(path)["pages"][0]
        self.assertEqual(page["image_size"], [36, 72])
        self.assertEqual(page["rendering"]["page_rotation_degrees"], 90)

    def test_pdf_selection_keeps_original_page_numbers(self):
        path = self.pdf_file(sizes=((72, 36), (36, 72), (72, 72)))
        result = self.reader.read(path, page_numbers=[3, 1])
        self.assertEqual(result["selected_pages"], [3, 1])
        self.assertEqual([p["page_number"] for p in result["pages"]], [3, 1])
        self.assertFalse(result["all_pages_processed"])

    def test_blank_pages_are_kept_with_no_text_status(self):
        path = self.pdf_file(sizes=((72, 36), (36, 72)))
        self.backend.side_effect = None
        self.backend.return_value = None
        result = self.reader.read(path)
        self.assertEqual([p["status"] for p in result["pages"]], ["NO_TEXT", "NO_TEXT"])
        self.assertEqual(result["processed_page_count"], 2)

    def test_image_page_failure_does_not_return_partial_document(self):
        path = self.image_file("multipage.tiff", frames=3)
        self.backend.side_effect = [SimpleNamespace(txts=None, scores=None, boxes=None),
                                    RuntimeError("test failure")]
        with self.assertRaisesRegex(RuntimeError, "document page 2") as error:
            self.reader.read(path)
        self.assertEqual(str(error.exception.__cause__), "test failure")
        self.assertEqual(self.backend.call_count, 2)

    def test_pdf_is_closed_on_ocr_failure(self):
        path = self.pdf_file()
        document = pdfium.PdfDocument(path)
        original_close = document.close
        self.backend.side_effect = RuntimeError("test failure")
        with patch.object(document, "close", wraps=original_close) as close:
            with patch("pypdfium2.PdfDocument", return_value=document):
                with self.assertRaisesRegex(RuntimeError, "document page 1"):
                    self.reader.read(path)
            close.assert_called_once()

    def test_render_pixel_limit_is_checked_before_native_allocation(self):
        path = self.pdf_file(sizes=((72, 72),))
        reader = DocumentOcrReader(RapidOcrEngine(OcrSettings(max_pixels=100)))
        with patch.object(pdfium.PdfPage, "render") as render:
            with self.assertRaisesRegex(ValueError, "max_pixels"):
                reader.read(path)
            render.assert_not_called()
        self.builder.assert_not_called()

    def test_image_pixel_limit_is_checked_before_inference(self):
        path = self.image_file()
        reader = DocumentOcrReader(RapidOcrEngine(OcrSettings(max_pixels=100)))
        with self.assertRaisesRegex(ValueError, "Image page 1"):
            reader.read(path)
        self.builder.assert_not_called()

    def test_full_document_is_never_silently_truncated(self):
        reader = DocumentOcrReader(self.engine, DocumentOcrSettings(max_pages=1))
        for path in (
            self.image_file("multipage.tiff", frames=2),
            self.pdf_file(sizes=((72, 36), (36, 72))),
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "max_pages"):
                    reader.read(path)
        self.builder.assert_not_called()

    def test_explicit_sample_can_be_selected_from_a_larger_document(self):
        path = self.pdf_file(sizes=((72, 36), (36, 72)))
        reader = DocumentOcrReader(self.engine, DocumentOcrSettings(max_pages=1))
        result = reader.read(path, page_numbers=[2])
        self.assertEqual(result["document_page_count"], 2)
        self.assertEqual(result["selected_pages"], [2])
        self.assertFalse(result["all_pages_processed"])

    def test_invalid_page_selection_is_rejected_before_inference(self):
        path = self.image_file("multipage.tiff", frames=2)
        for pages in ([], "1", [0], [3], [True], [1.5], [1, 1]):
            with self.subTest(pages=pages):
                with self.assertRaises(ValueError):
                    self.reader.read(path, page_numbers=pages)
        self.builder.assert_not_called()

    def test_source_byte_limit_is_enforced(self):
        path = self.image_file()
        reader = DocumentOcrReader(self.engine, DocumentOcrSettings(max_source_bytes=10))
        with self.assertRaisesRegex(ValueError, "max_source_bytes"):
            reader.read(path)
        self.builder.assert_not_called()

    def test_missing_empty_corrupt_and_html_sources_are_not_ocr_results(self):
        with self.assertRaises(FileNotFoundError):
            self.reader.read(self.root / "missing.png")
        path = self.root / "empty.png"
        path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.reader.read(path)
        path.write_bytes(b"<html><title>Just a moment...</title></html>")
        with self.assertRaises(UnidentifiedImageError):
            self.reader.read(path)
        path.write_bytes(b"%PDF-invalid data")
        with self.assertRaises(Exception):
            self.reader.read(path)
        self.builder.assert_not_called()

    def test_missing_pdf_dependency_is_reported_without_loading_ocr(self):
        import sys
        path = self.pdf_file()
        with patch.dict(sys.modules, {"pypdfium2": None}):
            with self.assertRaisesRegex(RuntimeError, "requirements-ocr.txt"):
                self.reader.read(path)
        self.builder.assert_not_called()

    def test_invalid_document_settings_are_rejected(self):
        for kwargs in (
            {"pdf_render_dpi": 0}, {"pdf_render_dpi": float("nan")},
            {"pdf_render_dpi": True}, {"max_pages": 0}, {"max_pages": True},
            {"max_source_bytes": 0}, {"max_source_bytes": 1.2},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    DocumentOcrSettings(**kwargs)
        with self.assertRaises(TypeError):
            DocumentOcrReader(engine="rapidocr")
        with self.assertRaises(TypeError):
            DocumentOcrReader(settings={})


if __name__ == "__main__":
    unittest.main()
