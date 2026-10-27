"""Save a discovered image as primary artifact with HTML/manifest sidecars."""

import hashlib
import io
import json
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image

from ingestion.bypassCollector.actions.baseAction import BaseAction
from ingestion.bypassCollector.browserBinaryDownload import fetch_browser_binary
from ingestion.urlResolver.htmlImageResolver import resolve_image_from_html


logger = logging.getLogger(__name__)
EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "TIFF": "tiff", "GIF": "gif", "BMP": "bmp"}


def _positive(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return value


class SaveImageAction(BaseAction):
    def execute(self, actionConfig, context, engine):
        context.pop("savedFile", None)
        context.pop("imageArtifact", None)
        published_paths = []
        published = False
        try:
            image_config = actionConfig.get("image")
            if not isinstance(image_config, dict):
                raise ValueError("save_image requires an image discovery config.")
            timeout = _positive(actionConfig.get("timeout_seconds", 45), "timeout_seconds")
            max_bytes = _positive(actionConfig.get("max_size_bytes", 10_000_000), "max_size_bytes")
            max_pixels = _positive(actionConfig.get("max_pixels", 40_000_000), "max_pixels")
            min_width = _positive(actionConfig.get("min_width", 1), "min_width")
            min_height = _positive(actionConfig.get("min_height", 1), "min_height")
            formats = actionConfig.get("allowed_formats", list(EXTENSIONS))
            if (not isinstance(formats, list) or not formats
                    or any(not isinstance(fmt, str) or fmt not in EXTENSIONS for fmt in formats)):
                raise ValueError("allowed_formats must name supported Pillow image formats.")

            html = engine.getHtml()
            page_url = engine.getCurrentUrl() or context.get("url")
            selected = resolve_image_from_html(html, page_url, image_config)
            logger.info("Selected image: %s (%s)", selected.url, selected.descriptor or selected.attribute)
            payload, response = fetch_browser_binary(
                engine, selected.url, page_url=page_url, timeout_seconds=timeout, max_bytes=max_bytes,
            )
            with Image.open(io.BytesIO(payload)) as image:
                image_format = image.format
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
                if image_format not in formats:
                    raise ValueError(f"Unexpected image format: {image_format}")
                if frames != 1:
                    raise ValueError("save_image currently requires one static image frame.")
                if width < min_width or height < min_height:
                    raise ValueError("Downloaded image is smaller than configured dimensions.")
                if width * height > max_pixels:
                    raise ValueError("Downloaded image exceeds max_pixels.")
                if selected.width_hint is not None and width != selected.width_hint:
                    raise ValueError("Image width differs from its srcset width descriptor.")
                image.verify()
            with Image.open(io.BytesIO(payload)) as image:
                image.load()  # Reject truncated payloads before publishing a capture.

            output_dir = Path(actionConfig.get("outputDir") or context.get("outputDir") or "data/downloads")
            output_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc)
            labels = [re.sub(r"[^\w.-]+", "_", str(context.get(key) or "source")).strip(".")
                      for key in ("source_name", "list_name")]
            stem = "_".join(labels + [stamp.strftime("%Y%m%dT%H%M%S_%fZ"), uuid4().hex[:8]])
            image_path = output_dir / (stem + "." + EXTENSIONS[image_format])
            html_path = output_dir / (stem + ".source.html")
            manifest_path = output_dir / (stem + ".manifest.json")
            html_bytes = html.encode("utf-8")
            manifest = {
                "schema_version": 1, "retrieved_at": stamp.isoformat(),
                "source_name": context.get("source_name"), "list_name": context.get("list_name"),
                "page_url": page_url, "image_url": selected.url,
                "final_image_url": response["final_url"], "response_content_type": response["content_type"],
                "selection": {"attribute": selected.attribute, "descriptor": selected.descriptor,
                              "config": image_config},
                "image": {"file": image_path.name, "sha256": hashlib.sha256(payload).hexdigest(),
                          "size_bytes": len(payload), "format": image_format,
                          "width": width, "height": height, "frames": frames},
                "html": {"file": html_path.name, "sha256": hashlib.sha256(html_bytes).hexdigest(),
                         "size_bytes": len(html_bytes)},
            }
            # Keep the existing day-directory contract and globally unique file
            # names. Publish the primary image last, after both sidecars exist.
            with tempfile.TemporaryDirectory(prefix="_attachments_pending_", dir=output_dir) as pending:
                pending = Path(pending)
                (pending / image_path.name).write_bytes(payload)
                (pending / html_path.name).write_bytes(html_bytes)
                (pending / manifest_path.name).write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                for path in (html_path, manifest_path, image_path):
                    if path.exists():
                        raise FileExistsError(f"Capture file already exists: {path}")
                    (pending / path.name).replace(path)
                    published_paths.append(path)
            context["savedFile"] = str(image_path)
            context["imageArtifact"] = {"image": str(image_path), "html": str(html_path),
                                        "manifest": str(manifest_path)}
            published = True
            logger.info("Image captured: %s; dimensions=%dx%d; bytes=%d", image_path, width, height, len(payload))
            return True
        except Exception:
            logger.exception("Image collection failed")
            return False
        finally:
            if not published:
                context.pop("savedFile", None)
                context.pop("imageArtifact", None)
                for path in published_paths:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        logger.exception("Could not remove incomplete capture file: %s", path)
