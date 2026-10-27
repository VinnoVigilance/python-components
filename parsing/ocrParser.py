"""Shared parser adapter: image/PDF -> document OCR -> layout-based raw records."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from parsing.ocr.ocrRecordExtractor import OcrRecordExtractor


ROOT_DIR = Path(__file__).resolve().parents[1]
IMAGE_TYPES = {"image", "img", "jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp", "gif"}


class OcrParser:
    """Match the existing parse(file_path, config) parser interface.

    Fresh recognition loads optional OCR dependencies only during parse().
    saved_ocr_path is an explicit replay option, not an automatic OCR cache.
    """

    def parse(self, file_path, config=None):
        source_path = Path(file_path).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"OCR source file not found: {source_path}")
        config = config or {}
        if not isinstance(config, dict):
            raise ValueError("OCR parser config must be an object.")
        settings = config.get("parser")
        if not isinstance(settings, dict):
            raise ValueError("OCR source must define a parser object and layout_path/layout.")
        allowed = {"type", "mode", "layout_path", "layout", "engine", "document",
                   "page_numbers", "saved_ocr_path", "runtime"}
        unknown = set(settings) - allowed
        if unknown:
            raise ValueError(f"Unknown OCR parser settings: {sorted(unknown)}")
        mode = str(settings.get("mode", "ocr")).strip().lower()
        if mode != "ocr":
            raise ValueError("OcrParser supports only parser.mode='ocr'.")
        if ("layout_path" in settings) == ("layout" in settings):
            raise ValueError("Define exactly one of parser.layout_path or parser.layout.")
        if "layout_path" in settings:
            layout_path = self._resolve_path(settings["layout_path"], "layout_path")
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
        else:
            layout = deepcopy(settings["layout"])
        extractor = OcrRecordExtractor(layout)

        from parsing.ocr.subprocessOcr import validate_runtime
        runtime = validate_runtime(settings.get("runtime"))

        # Parser construction/factory imports remain independent of OCR packages.
        from parsing.ocr.documentOcrReader import DocumentOcrReader, DocumentOcrSettings
        from parsing.ocr.rapidOcrEngine import OcrSettings, RapidOcrEngine

        engine_config = settings.get("engine", {})
        document_config = settings.get("document", {})
        if not isinstance(engine_config, dict) or not isinstance(document_config, dict):
            raise ValueError("parser.engine and parser.document must be objects.")
        try:
            engine_settings = OcrSettings(**engine_config)
            document_settings = DocumentOcrSettings(**document_config)
        except TypeError as exc:
            raise ValueError(f"Unknown OCR engine/document setting: {exc}") from exc

        requested = settings.get("page_numbers")
        if requested is not None and (
            not isinstance(requested, (list, tuple)) or not requested
            or any(type(number) is not int or number < 1 for number in requested)
            or len(set(requested)) != len(requested)
        ):
            raise ValueError("parser.page_numbers must be unique positive page numbers.")

        replay_path = None
        if "saved_ocr_path" in settings:
            replay_path = self._resolve_path(settings["saved_ocr_path"], "saved_ocr_path")
            document = json.loads(replay_path.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("Saved OCR must be a document object.")
            digest, size = self._fingerprint(source_path, document_settings.max_source_bytes)
            if document.get("source_sha256") != digest:
                raise ValueError("Saved OCR source hash does not match the input file.")
            if document.get("source_size_bytes") != size:
                raise ValueError("Saved OCR source size does not match the input file.")
            if requested is not None and document.get("selected_pages") != list(requested):
                raise ValueError("Saved OCR selected pages do not match parser.page_numbers.")
            previous_source_path = document.get("source_path")
            document["source_path"] = str(source_path)
        else:
            previous_source_path = None
            if runtime["mode"] == "subprocess":
                from parsing.ocr.subprocessOcr import read_document_in_subprocess
                document = read_document_in_subprocess(
                    source_path, engine_settings, document_settings, requested, runtime,
                )
            else:
                reader = DocumentOcrReader(RapidOcrEngine(engine_settings), document_settings)
                document = reader.read(source_path, page_numbers=requested)

        file_type = str(config.get("file_type", "")).strip().lower()
        expected_kind = "image" if file_type in IMAGE_TYPES else "pdf" if file_type == "pdf" else None
        if expected_kind and document.get("document_type") != expected_kind:
            raise ValueError(f"Expected {expected_kind} content for declared file_type={file_type!r}.")

        # First validate the common OCR schema/coverage through the extractor.
        records = extractor.extract(document)
        if len(document["pages"]) > document_settings.max_pages:
            raise ValueError("OCR document exceeds parser.document.max_pages.")
        for page in document["pages"]:
            if page["image_size"][0] * page["image_size"][1] > engine_settings.max_pixels:
                raise ValueError("OCR page exceeds parser.engine.max_pixels.")
            if replay_path is not None or runtime["mode"] == "subprocess":
                for key in engine_config:
                    if (page.get("settings") or {}).get(key) != getattr(engine_settings, key):
                        raise ValueError(f"Saved OCR engine setting differs: {key}.")
                if document.get("document_type") == "pdf" and "pdf_render_dpi" in document_config:
                    if (page.get("rendering") or {}).get("requested_dpi") != document_settings.pdf_render_dpi:
                        raise ValueError("Saved OCR render resolution differs from configured DPI.")
        for record in records:
            record["_ocr"]["recognition_mode"] = "replay" if replay_path else "fresh"
            if replay_path is None:
                record["_ocr"]["execution_mode"] = runtime["mode"]
            if replay_path:
                record["_ocr"]["saved_ocr_path"] = str(replay_path)
                record["_ocr"]["ocr_document_source_path"] = previous_source_path
        return records

    @staticmethod
    def _resolve_path(value, label):
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError(f"parser.{label} must be a non-empty file path.")
        path = Path(value)
        return (path if path.is_absolute() else ROOT_DIR / path).resolve()

    @staticmethod
    def _fingerprint(path, limit):
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError("Source exceeds parser.document.max_source_bytes.")
                digest.update(chunk)
        return digest.hexdigest(), size
