"""Recognize one image or rendered document page without source business rules.

Coordinates refer to the full, EXIF-oriented image, before cropping/resizing.
An OCR line is evidence, not an approved watchlist record or a personal ID.
"""

import hashlib
import math
import threading
import time
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from numbers import Real

from PIL import Image, ImageOps


@dataclass(frozen=True)
class OcrSettings:
    """Enum names are resolved against the installed RapidOCR version."""

    recognition_language: str = "EN"
    recognition_version: str = "PPOCRV5"
    recognition_model_type: str = "MOBILE"
    detection_version: str = "PPOCRV6"
    detection_model_type: str = "SMALL"
    model_root_dir: str | None = None
    max_pixels: int = 40_000_000

    def __post_init__(self):
        for name in (
            "recognition_language", "recognition_version",
            "recognition_model_type", "detection_version",
            "detection_model_type",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty enum name.")
            object.__setattr__(self, name, value.strip().upper())
        if (
            not isinstance(self.max_pixels, int)
            or isinstance(self.max_pixels, bool)
            or self.max_pixels < 1
        ):
            raise ValueError("max_pixels must be a positive integer.")
        if self.model_root_dir is not None and (
            not isinstance(self.model_root_dir, str)
            or not self.model_root_dir.strip()
        ):
            raise ValueError("model_root_dir must be a non-empty path or None.")


class RapidOcrEngine:
    """Reuse one lazily loaded CPU engine; calls on one instance are serialized.

    Input: a Pillow image, including a PDF page rendered by a future adapter.
    Output: JSON-compatible raw lines, scores, coordinates and provenance.
    No names, aliases, case numbers or record identities are inferred here.
    """

    def __init__(self, settings: OcrSettings | None = None):
        if settings is not None and not isinstance(settings, OcrSettings):
            raise TypeError("settings must be OcrSettings or None.")
        self.settings = settings or OcrSettings()
        self._backend = None
        self._lock = threading.Lock()

    def _build_backend(self):
        try:
            from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                "OCR dependencies are unavailable. "
                "Install requirements-ocr.txt in the OCR runtime."
            ) from exc

        def member(enum_class, name, field):
            try:
                return enum_class[name]
            except KeyError as exc:
                raise ValueError(
                    f"Unsupported {field}: {name!r} for installed RapidOCR."
                ) from exc

        params = {
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Cls.engine_type": EngineType.ONNXRUNTIME,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": member(
                OCRVersion, self.settings.detection_version, "detection_version"
            ),
            "Det.model_type": member(
                ModelType, self.settings.detection_model_type, "detection_model_type"
            ),
            "Rec.lang_type": member(
                LangRec, self.settings.recognition_language, "recognition_language"
            ),
            "Rec.ocr_version": member(
                OCRVersion, self.settings.recognition_version, "recognition_version"
            ),
            "Rec.model_type": member(
                ModelType, self.settings.recognition_model_type,
                "recognition_model_type",
            ),
        }
        if self.settings.model_root_dir is not None:
            params["Global.model_root_dir"] = self.settings.model_root_dir
        return RapidOCR(params=params)

    def recognize(self, image, *, page_number=1, region=None, scale=1.0):
        """OCR a page/region, preserving raw strings and low-scoring output.

        region: optional integer (left, top, right, bottom) in upright pixels.
        scale: positive resize factor; fractional rounding is handled per axis.
        Blank output has status NO_TEXT. Backend failures are raised to callers.
        This method does not determine whether any output is safe for Core.
        """
        if not isinstance(image, Image.Image):
            raise TypeError("image must be a Pillow Image.Image.")
        if (
            not isinstance(page_number, int) or isinstance(page_number, bool)
            or page_number < 1
        ):
            raise ValueError("page_number must be a positive integer.")
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("Pass multi-frame images as separate page images.")
        if image.width < 1 or image.height < 1:
            raise ValueError("Input image must have positive dimensions.")
        if image.width * image.height > self.settings.max_pixels:
            raise ValueError("Input image exceeds max_pixels.")
        scale = self._number(scale, "scale")
        if scale <= 0:
            raise ValueError("scale must be positive.")

        upright = ImageOps.exif_transpose(image)
        if "A" in upright.getbands() or "transparency" in upright.info:
            rgba = upright.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            upright = Image.alpha_composite(white, rgba).convert("RGB")
        else:
            upright = upright.convert("RGB")

        width, height = upright.size
        left, top, right, bottom = self._region(region, width, height)
        crop_width, crop_height = right - left, bottom - top
        if (
            crop_width * scale > self.settings.max_pixels
            or crop_height * scale > self.settings.max_pixels
        ):
            raise ValueError("Processed image exceeds max_pixels.")
        processed_width = round(crop_width * scale)
        processed_height = round(crop_height * scale)
        if processed_width < 1 or processed_height < 1:
            raise ValueError("scale produces an empty image.")
        if processed_width * processed_height > self.settings.max_pixels:
            raise ValueError("Processed image exceeds max_pixels.")

        fingerprint = hashlib.sha256()
        fingerprint.update(f"RGB:{width}:{height}:".encode("ascii"))
        fingerprint.update(upright.tobytes())
        working = upright.crop((left, top, right, bottom))
        if working.size != (processed_width, processed_height):
            working = working.resize(
                (processed_width, processed_height), Image.Resampling.LANCZOS
            )
        scale_x = processed_width / crop_width
        scale_y = processed_height / crop_height

        with self._lock:
            if self._backend is None:
                self._backend = self._build_backend()
            started = time.perf_counter()
            output = self._backend(working, text_score=0.0)
            lines = self._convert_lines(output, left, top, scale_x, scale_y)
            elapsed = time.perf_counter() - started

        return {
            "schema_version": 1,
            "engine": "rapidocr",
            "engine_version": self._version("rapidocr"),
            "runtime_version": self._version("onnxruntime"),
            "pillow_version": self._version("Pillow"),
            "settings": asdict(self.settings),
            "page_number": page_number,
            "coordinate_space": "upright_image_pixels",
            "image_size": [width, height],
            "pixel_sha256": fingerprint.hexdigest(),
            "region": [left, top, right, bottom],
            "processed_size": [processed_width, processed_height],
            "scale_xy": [scale_x, scale_y],
            "status": "TEXT_FOUND" if lines else "NO_TEXT",
            "elapsed_seconds": elapsed,
            "line_count": len(lines),
            "lines": lines,
        }

    @staticmethod
    def _version(package):
        try:
            return version(package)
        except PackageNotFoundError:
            return None

    @staticmethod
    def _number(value, field):
        if not isinstance(value, Real) or isinstance(value, bool):
            raise ValueError(f"{field} must be a finite number.")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number.")
        return value

    @staticmethod
    def _region(region, width, height):
        if region is None:
            return 0, 0, width, height
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ValueError("region must contain four integer pixel coordinates.")
        if any(not isinstance(v, int) or isinstance(v, bool) for v in region):
            raise ValueError("region coordinates must be integers.")
        left, top, right, bottom = region
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("region must be non-empty and inside the upright image.")
        return left, top, right, bottom

    def _convert_lines(self, output, left, top, scale_x, scale_y):
        if output is None:
            return []
        if any(not hasattr(output, key) for key in ("txts", "scores", "boxes")):
            raise RuntimeError("Unexpected OCR backend output.")
        texts, scores, boxes = output.txts, output.scores, output.boxes
        if texts is None and scores is None and boxes is None:
            return []
        if any(value is None for value in (texts, scores, boxes)):
            raise RuntimeError("Incomplete OCR backend output.")
        if not (len(texts) == len(scores) == len(boxes)):
            raise RuntimeError("OCR text, score and box counts differ.")

        lines = []
        for index, (text, score, box) in enumerate(zip(texts, scores, boxes), 1):
            if not isinstance(text, str):
                raise RuntimeError("OCR backend returned a non-string text.")
            score = self._number(score, "OCR score")
            if not 0 <= score <= 1:
                raise ValueError("OCR score must lie between 0 and 1.")
            if len(box) != 4 or any(len(point) != 2 for point in box):
                raise RuntimeError("OCR box must contain four two-dimensional points.")
            points = [
                [
                    left + self._number(x, "box x") / scale_x,
                    top + self._number(y, "box y") / scale_y,
                ]
                for x, y in box
            ]
            lines.append({
                "line_number": index, "text": text, "score": score, "box": points,
            })
        return lines
