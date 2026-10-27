"""OCR adapter contracts, without model downloads or recognition accuracy claims."""

import json
import subprocess
import sys
import unittest
from enum import Enum
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from parsing.ocr.rapidOcrEngine import OcrSettings, RapidOcrEngine


REAL_BUILD = RapidOcrEngine._build_backend


def output(texts=("  SAMPLE NAME  ",), scores=(0.9,), boxes=None):
    if boxes is None:
        boxes = [((0, 0), (20, 0), (20, 10), (0, 10))] * len(texts)
    return SimpleNamespace(txts=texts, scores=scores, boxes=boxes)


class TestRapidOcrEngine(unittest.TestCase):
    def setUp(self):
        self.backend = Mock(return_value=output())
        self.builder = patch.object(
            RapidOcrEngine, "_build_backend", return_value=self.backend
        ).start()
        self.addCleanup(patch.stopall)
        self.engine = RapidOcrEngine()
        self.image = Image.new("RGB", (100, 80), "white")

    def test_raw_text_and_low_scores_are_not_corrected_or_filtered(self):
        self.backend.return_value = output(
            ("  SAMPLE NAME  ", "CC? 01-XX", ""), (0.99, 0.02, 0.0)
        )
        result = self.engine.recognize(self.image)
        self.assertEqual(
            [line["text"] for line in result["lines"]],
            ["  SAMPLE NAME  ", "CC? 01-XX", ""],
        )
        self.assertEqual([x["score"] for x in result["lines"]], [0.99, 0.02, 0.0])
        self.assertEqual(self.backend.call_args.kwargs, {"text_score": 0.0})

    def test_crop_and_upscale_coordinates_return_to_the_full_image(self):
        self.backend.return_value = output(
            boxes=[((0, 0), (60, 0), (60, 30), (0, 30))]
        )
        result = self.engine.recognize(
            self.image, page_number=7, region=(10, 20, 40, 40), scale=3
        )
        self.assertEqual(result["page_number"], 7)
        self.assertEqual(result["processed_size"], [90, 60])
        self.assertEqual(result["lines"][0]["box"],
                         [[10, 20], [30, 20], [30, 30], [10, 30]])

    def test_fractional_scale_uses_actual_rounded_size_per_axis(self):
        self.backend.return_value = output(
            boxes=[((0, 0), (4, 0), (4, 8), (0, 8))]
        )
        result = self.engine.recognize(
            self.image, region=(10, 20, 13, 25), scale=1.5
        )
        self.assertEqual(result["processed_size"], [4, 8])
        self.assertEqual(result["lines"][0]["box"],
                         [[10, 20], [13, 20], [13, 25], [10, 25]])

    def test_backend_is_lazy_and_reused_across_pages(self):
        self.builder.assert_not_called()
        self.engine.recognize(self.image, page_number=1)
        self.engine.recognize(self.image, page_number=2)
        self.builder.assert_called_once()
        self.assertEqual(self.backend.call_count, 2)

    def test_model_configuration_is_validated_before_first_inference(self):
        fake = self._fake_rapidocr()
        engine = RapidOcrEngine(OcrSettings(model_root_dir="models/ocr"))
        with patch.dict(sys.modules, {"rapidocr": fake}):
            REAL_BUILD(engine)
        params = fake.RapidOCR.call_args.kwargs["params"]
        self.assertEqual(params["Rec.lang_type"], fake.LangRec.EN)
        self.assertEqual(params["Rec.ocr_version"], fake.OCRVersion.PPOCRV5)
        self.assertEqual(params["Det.ocr_version"], fake.OCRVersion.PPOCRV6)
        self.assertEqual(params["Rec.model_type"], fake.ModelType.MOBILE)
        self.assertEqual(params["Global.model_root_dir"], "models/ocr")
        for stage in ("Det", "Cls", "Rec"):
            self.assertEqual(params[f"{stage}.engine_type"], fake.EngineType.ONNXRUNTIME)

    def test_unsupported_model_enum_has_an_actionable_error(self):
        fake = self._fake_rapidocr()
        engine = RapidOcrEngine(OcrSettings(recognition_language="NOT_A_LANGUAGE"))
        with patch.dict(sys.modules, {"rapidocr": fake}):
            with self.assertRaisesRegex(ValueError, "recognition_language"):
                REAL_BUILD(engine)
        fake.RapidOCR.assert_not_called()

    def test_missing_optional_package_has_an_actionable_error(self):
        with patch.dict(sys.modules, {"rapidocr": None}):
            with self.assertRaisesRegex(RuntimeError, "requirements-ocr.txt"):
                REAL_BUILD(self.engine)

    def test_adapter_import_does_not_import_ocr_runtime(self):
        code = (
            "import sys; sys.modules['rapidocr'] = None; "
            "sys.modules['onnxruntime'] = None; "
            "from parsing.ocr.rapidOcrEngine import RapidOcrEngine; "
            "RapidOcrEngine()"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_settings_normalize_enum_names(self):
        settings = OcrSettings(recognition_language=" en ")
        self.assertEqual(settings.recognition_language, "EN")

    def test_invalid_settings_are_rejected(self):
        for kwargs in (
            {"recognition_language": ""}, {"recognition_version": None},
            {"max_pixels": 0}, {"max_pixels": True}, {"max_pixels": 1.5},
            {"model_root_dir": " "},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    OcrSettings(**kwargs)
        with self.assertRaises(TypeError):
            RapidOcrEngine({"recognition_language": "EN"})

    def test_invalid_page_numbers_are_rejected(self):
        for number in (0, -1, True, "1", 1.5):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    self.engine.recognize(self.image, page_number=number)
        self.builder.assert_not_called()

    def test_invalid_scales_are_rejected_before_loading_models(self):
        for scale in (0, -1, True, "3", float("nan"), float("inf"), 0.001):
            with self.subTest(scale=scale):
                with self.assertRaises(ValueError):
                    self.engine.recognize(self.image, scale=scale)
        self.builder.assert_not_called()

    def test_invalid_regions_are_rejected(self):
        for region in (
            (0, 0, 0, 10), (-1, 0, 10, 10), (0, 0, 101, 10),
            (0, 0, 10, 81), (0, 0, 10), (0, 0, 10.5, 20),
            (False, 0, 10, 20), "0,0,10,20",
        ):
            with self.subTest(region=region):
                with self.assertRaises(ValueError):
                    self.engine.recognize(self.image, region=region)
        self.builder.assert_not_called()

    def test_wrong_input_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.recognize("https://example.org/document.jpg")
        self.builder.assert_not_called()

    def test_multi_frame_input_is_not_silently_truncated(self):
        with patch.object(self.image, "n_frames", 2, create=True):
            with self.assertRaisesRegex(ValueError, "separate page"):
                self.engine.recognize(self.image)

    def test_pixel_limits_apply_before_and_after_upscaling(self):
        engine = RapidOcrEngine(OcrSettings(max_pixels=100))
        with self.assertRaisesRegex(ValueError, "Input image"):
            engine.recognize(self.image)
        with self.assertRaisesRegex(ValueError, "Processed image"):
            engine.recognize(Image.new("RGB", (10, 10)), scale=2)
        self.builder.assert_not_called()

    def test_extreme_finite_scale_does_not_overflow_during_rounding(self):
        with self.assertRaisesRegex(ValueError, "Processed image"):
            self.engine.recognize(self.image, scale=1e308)
        self.builder.assert_not_called()

    def test_zero_sized_input_is_rejected_before_inference(self):
        with self.assertRaisesRegex(ValueError, "positive dimensions"):
            self.engine.recognize(Image.new("RGB", (0, 80)))
        self.builder.assert_not_called()

    def test_blank_output_is_explicit_and_does_not_claim_approval(self):
        for blank in (None, output((), (), []),
                      SimpleNamespace(txts=None, scores=None, boxes=None)):
            with self.subTest(blank=blank):
                self.backend.return_value = blank
                result = self.engine.recognize(self.image)
                self.assertEqual(result["status"], "NO_TEXT")
                self.assertEqual(result["lines"], [])
                self.assertEqual(result["line_count"], 0)
                self.assertNotIn("approved", result)

    def test_partial_or_mismatched_output_is_not_silently_zipped(self):
        for invalid in (
            SimpleNamespace(txts=("a",), scores=None, boxes=[]),
            output(("a", "b"), (0.9,)), object(),
        ):
            with self.subTest(invalid=invalid):
                self.backend.return_value = invalid
                with self.assertRaises(RuntimeError):
                    self.engine.recognize(self.image)

    def test_invalid_scores_are_rejected(self):
        for score in (-0.01, 1.01, True, "0.9", float("nan"), float("inf")):
            with self.subTest(score=score):
                self.backend.return_value = output(scores=(score,))
                with self.assertRaises(ValueError):
                    self.engine.recognize(self.image)

    def test_invalid_geometry_is_rejected(self):
        for box in (
            ((0, 0), (1, 1)),
            ((0, 0, 0),) * 4,
            ((float("nan"), 0),) * 4,
            ((float("inf"), 0),) * 4,
        ):
            with self.subTest(box=box):
                self.backend.return_value = output(boxes=[box])
                with self.assertRaises((RuntimeError, ValueError)):
                    self.engine.recognize(self.image)

    def test_non_string_text_is_rejected(self):
        self.backend.return_value = output(texts=(None,))
        with self.assertRaises(RuntimeError):
            self.engine.recognize(self.image)

    def test_inference_failure_propagates_and_lock_is_released(self):
        self.backend.side_effect = [RuntimeError("inference failed"), output()]
        with self.assertRaisesRegex(RuntimeError, "inference failed"):
            self.engine.recognize(self.image)
        self.assertEqual(self.engine.recognize(self.image)["status"], "TEXT_FOUND")

    def test_image_is_not_modified(self):
        original_bytes = self.image.tobytes()
        self.engine.recognize(self.image, region=(10, 20, 40, 40), scale=3)
        self.assertEqual(self.image.size, (100, 80))
        self.assertEqual(self.image.tobytes(), original_bytes)

    def test_exif_orientation_has_an_explicit_coordinate_space(self):
        image = Image.new("RGB", (100, 80), "white")
        image.getexif()[274] = 6
        result = self.engine.recognize(image)
        self.assertEqual(result["image_size"], [80, 100])
        self.assertEqual(result["coordinate_space"], "upright_image_pixels")
        self.assertEqual(image.getexif()[274], 6)

    def test_transparency_is_composited_on_white(self):
        image = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
        self.engine.recognize(image)
        processed = self.backend.call_args.args[0]
        self.assertEqual(processed.mode, "RGB")
        self.assertEqual(processed.getpixel((0, 0)), (255, 255, 255))

    def test_fingerprint_identifies_full_page_and_result_is_json_compatible(self):
        first = self.engine.recognize(self.image)
        second = self.engine.recognize(self.image, region=(10, 20, 40, 40), scale=3)
        different = self.engine.recognize(Image.new("RGB", (100, 80), "black"))
        self.assertEqual(first["pixel_sha256"], second["pixel_sha256"])
        self.assertNotEqual(first["pixel_sha256"], different["pixel_sha256"])
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)
        self.assertNotIn("source_record_id", first)

    @staticmethod
    def _fake_rapidocr():
        fake = ModuleType("rapidocr")
        fake.LangRec = Enum("LangRec", {"EN": "en"})
        fake.ModelType = Enum("ModelType", {"MOBILE": "mobile", "SMALL": "small"})
        fake.OCRVersion = Enum("OCRVersion", {"PPOCRV5": "v5", "PPOCRV6": "v6"})
        fake.EngineType = Enum("EngineType", {"ONNXRUNTIME": "onnxruntime"})
        fake.RapidOCR = Mock()
        return fake


if __name__ == "__main__":
    unittest.main()
