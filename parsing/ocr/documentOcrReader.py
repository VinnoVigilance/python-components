"""Read image frames, PDF pages or self-contained HTML images for OCR.

This is an explicit OCR path, not automatic fallback for existing PDF parsers.
All processing uses one immutable source snapshot. No records are written here.
"""

import base64
import hashlib
import io
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from parsing.ocr.rapidOcrEngine import RapidOcrEngine


# PDFium is not thread-safe; applications should prefer separate processes.
_PDFIUM_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentOcrSettings:
    pdf_render_dpi: float = 200
    max_pages: int = 500
    max_source_bytes: int = 100 * 1024 * 1024
    recognition_scale: float = 1

    def __post_init__(self):
        dpi = RapidOcrEngine._number(self.pdf_render_dpi, "pdf_render_dpi")
        if dpi <= 0:
            raise ValueError("pdf_render_dpi must be positive.")
        object.__setattr__(self, "pdf_render_dpi", dpi)
        scale = RapidOcrEngine._number(self.recognition_scale, "recognition_scale")
        if scale <= 0:
            raise ValueError("recognition_scale must be positive.")
        object.__setattr__(self, "recognition_scale", scale)
        for field in ("max_pages", "max_source_bytes"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{field} must be a positive integer.")


class DocumentOcrReader:
    """OCR complete selected pages, retaining source and page provenance.

    PDF pages are rendered at configured resolution; image files retain their
    native coordinate space. recognition_scale resizes only the recognition
    input; returned coordinates and source fingerprints retain their meaning.
    Multi-frame image files are treated as multiple pages.
    On any page failure, read() raises instead of returning a partial result.
    """

    def __init__(self, engine=None, settings=None):
        if engine is not None and not isinstance(engine, RapidOcrEngine):
            raise TypeError("engine must be RapidOcrEngine or None.")
        if settings is not None and not isinstance(settings, DocumentOcrSettings):
            raise TypeError("settings must be DocumentOcrSettings or None.")
        self.engine = engine or RapidOcrEngine()
        self.settings = settings or DocumentOcrSettings()

    def read(self, file_path, *, page_numbers=None):
        """Return JSON-compatible document/page output, or raise on failure.

        page_numbers: optional non-empty list/tuple of unique 1-based pages.
        Selection preserves requested order and is always explicit in output.
        Pixel coordinates are image coordinates, not PDF canvas coordinates.
        """
        path = Path(file_path).resolve()
        with path.open("rb") as handle:
            data = handle.read(self.settings.max_source_bytes + 1)
        if not data:
            raise ValueError("Source document is empty.")
        if len(data) > self.settings.max_source_bytes:
            raise ValueError("Source document exceeds max_source_bytes.")
        source_hash = hashlib.sha256(data).hexdigest()

        # Identify supported images first: a PDF marker inside image metadata
        # must not accidentally turn an otherwise valid image into a PDF input.
        try:
            with io.BytesIO(data) as stream, Image.open(stream):
                pass
            kind = "image"
        except UnidentifiedImageError:
            prefix = data[:1024].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
            if prefix.startswith((b"<!doctype html", b"<html")):
                kind = "html"
            elif b"%PDF-" in data[:1024]:
                kind = "pdf"
            else:
                raise

        if kind == "pdf":
            with _PDFIUM_LOCK:
                count, selected, pages = self._read_pdf(data, page_numbers)
            image_format = None
        elif kind == "html":
            count, selected, pages = self._read_html(data, page_numbers)
            image_format = None
        else:
            count, selected, pages, image_format = self._read_image(data, page_numbers)

        for page in pages:
            page["rendering"]["recognition_scale"] = self.settings.recognition_scale

        return {
            "schema_version": 1,
            "source_path": str(path),
            "source_sha256": source_hash,
            "source_size_bytes": len(data),
            "document_type": kind,
            "image_format": image_format,
            "document_page_count": count,
            "selected_pages": selected,
            "processed_page_count": len(pages),
            "all_pages_processed": len(selected) == count,
            "pages": pages,
        }

    def _select_pages(self, count, requested):
        if count < 1:
            raise ValueError("Document contains no pages.")
        if requested is None:
            if count > self.settings.max_pages:
                raise ValueError("Document exceeds max_pages; no truncation is allowed.")
            return list(range(1, count + 1))
        if not isinstance(requested, (list, tuple)) or not requested:
            raise ValueError("page_numbers must be a non-empty list or tuple.")
        if len(requested) > self.settings.max_pages:
            raise ValueError("Selected pages exceed max_pages.")
        if any(
            not isinstance(number, int) or isinstance(number, bool)
            or not 1 <= number <= count
            for number in requested
        ):
            raise ValueError("page_numbers must be valid 1-based page integers.")
        if len(set(requested)) != len(requested):
            raise ValueError("page_numbers must not contain duplicate pages.")
        return list(requested)

    def _recognize(self, image, number):
        try:
            return self.engine.recognize(
                image, page_number=number, scale=self.settings.recognition_scale,
            )
        except Exception as exc:
            raise RuntimeError(f"OCR failed on document page {number}.") from exc

    def _read_image(self, data, requested):
        pages = []
        with io.BytesIO(data) as stream, Image.open(stream) as document:
            count = getattr(document, "n_frames", 1)
            selected = self._select_pages(count, requested)
            image_format = document.format
            for number in selected:
                logger.info("Reading image page %s/%s", number, count)
                document.seek(number - 1)
                width, height = document.size
                if width * height > self.engine.settings.max_pixels:
                    raise ValueError(f"Image page {number} exceeds max_pixels.")
                # Detach the decoded frame; the engine receives a single page.
                with document.copy() as page_image:
                    result = self._recognize(page_image, number)
                result["rendering"] = {
                    "method": "native_image",
                    "pillow_version": RapidOcrEngine._version("Pillow"),
                }
                pages.append(result)
                logger.info("Image page %s finished: %s lines", number, result["line_count"])
        return count, selected, pages, image_format

    def _read_html(self, data, requested):
        """Read only embedded-images-v1 snapshots; never fetch image URLs."""
        from lxml import html

        # The source byte limit is checked before parsing. Large data-URI
        # attributes need huge_tree; network loading and script execution are
        # not involved in this parser.
        root = html.fromstring(data, parser=html.HTMLParser(
            encoding="utf-8", no_network=True, huge_tree=True,
        ))
        if root.xpath("//meta[@name='snapshot-format']/@content") != ["embedded-images-v1"]:
            raise ValueError("HTML OCR requires an embedded-images-v1 snapshot.")
        containers = root.xpath("//main[@data-ocr-images='1']")
        if len(containers) != 1:
            raise ValueError("HTML snapshot requires exactly one image container.")
        figures = containers[0].xpath("./figure")
        if not figures or len(root.xpath("//img")) != len(figures):
            raise ValueError("HTML snapshot must contain one embedded image per figure.")
        if root.xpath("//script | //iframe | //object | //embed | //link"):
            raise ValueError("HTML snapshot contains unsupported active/external content.")

        mime_formats = {"data:image/png;base64": "PNG", "data:image/jpeg;base64": "JPEG",
                        "data:image/webp;base64": "WEBP"}
        sources = []
        for number, figure in enumerate(figures, 1):
            images = figure.xpath("./img")
            if figure.get("data-image-index") != str(number) or len(images) != 1:
                raise ValueError("HTML image indexes must be consecutive and unique.")
            image = images[0]
            header, separator, encoded = image.get("src", "").partition(",")
            if not separator or header not in mime_formats or not encoded:
                raise ValueError("HTML snapshot requires embedded PNG/JPEG/WEBP data URIs.")
            try:
                dimensions = (int(image.get("width", "")), int(image.get("height", "")))
            except ValueError as exc:
                raise ValueError("HTML image requires explicit integer dimensions.") from exc
            if min(dimensions) < 1:
                raise ValueError("HTML image dimensions must be positive.")
            sources.append((encoded, mime_formats[header], dimensions, figure.get("data-source-url")))

        count = len(sources)
        selected = self._select_pages(count, requested)
        pages = []
        for number in selected:
            logger.info("Reading embedded HTML image %s/%s", number, count)
            encoded, expected_format, declared_size, source_url = sources[number - 1]
            try:
                payload = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ValueError(f"Invalid embedded image base64 on page {number}.") from exc
            with Image.open(io.BytesIO(payload)) as image:
                image_format, size = image.format, image.size
                if image_format != expected_format or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Embedded image MIME type/format or frame count is invalid.")
                if size != declared_size:
                    raise ValueError("Embedded image dimensions differ from the HTML metadata.")
                if size[0] * size[1] > self.engine.settings.max_pixels:
                    raise ValueError(f"HTML image page {number} exceeds max_pixels.")
                image.verify()
            with Image.open(io.BytesIO(payload)) as image, image.copy() as page_image:
                result = self._recognize(page_image, number)
            result["rendering"] = {
                "method": "embedded_html_image", "image_format": image_format,
                "source_image_url": source_url,
                "source_image_sha256": hashlib.sha256(payload).hexdigest(),
                "source_image_size_bytes": len(payload),
                "pillow_version": RapidOcrEngine._version("Pillow"),
            }
            pages.append(result)
            logger.info("HTML image page %s finished: %s lines", number, result["line_count"])
        return count, selected, pages

    def _read_pdf(self, data, requested):
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise RuntimeError(
                "PDF rendering dependency is unavailable. "
                "Install the OCR dependencies from requirements.txt "
                "in the Python environment running this pipeline."
            ) from exc

        pages = []
        document = pdfium.PdfDocument(data)
        try:
            count = len(document)
            selected = self._select_pages(count, requested)
            document.init_forms()
            render_scale = self.settings.pdf_render_dpi / 72
            for number in selected:
                logger.info("Rendering PDF page %s/%s", number, count)
                page = document[number - 1]
                try:
                    width, height = page.get_size()
                    self._check_render_size(width, height, render_scale, number)
                    rotation = page.get_rotation()
                    bitmap = page.render(
                        scale=render_scale,
                        rotation=0,  # PDF's own page rotation is already honored.
                        fill_color=(255, 255, 255, 255),
                        draw_annots=True,
                        may_draw_forms=True,
                    )
                    try:
                        # PIL may share the native bitmap buffer. Copy before close.
                        with bitmap.to_pil() as view:
                            page_image = view.copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                try:
                    result = self._recognize(page_image, number)
                finally:
                    page_image.close()
                result["rendering"] = {
                    "method": "pdf_page_render",
                    "renderer": "pypdfium2",
                    "renderer_version": RapidOcrEngine._version("pypdfium2"),
                    "requested_dpi": self.settings.pdf_render_dpi,
                    "render_scale": render_scale,
                    "page_size_canvas_units": [float(width), float(height)],
                    "page_rotation_degrees": rotation,
                }
                pages.append(result)
                logger.info("PDF page %s finished: %s lines", number, result["line_count"])
        finally:
            document.close()
        return count, selected, pages

    def _check_render_size(self, width, height, scale, number):
        target_width, target_height = width * scale, height * scale
        if (
            not math.isfinite(target_width) or not math.isfinite(target_height)
            or target_width <= 0 or target_height <= 0
            or target_width > self.engine.settings.max_pixels
            or target_height > self.engine.settings.max_pixels
        ):
            raise ValueError(
                f"PDF page {number} has invalid render dimensions or exceeds max_pixels."
            )
        if math.ceil(target_width) * math.ceil(target_height) > self.engine.settings.max_pixels:
            raise ValueError(f"PDF page {number} exceeds max_pixels before rendering.")
