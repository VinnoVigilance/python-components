"""
Saving actions for the bypass collector.
"""

import base64
import hashlib
import json
import logging
import re

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pdfplumber

from ingestion.bypassCollector.actions.baseAction import BaseAction


logger = logging.getLogger(__name__)


class SaveHtmlAction(BaseAction):
    """Save the browser DOM, or an opt-in self-contained image snapshot."""

    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any,
    ) -> bool:
        if "embed_images" in actionConfig:
            return self._save_embedded_images(actionConfig, context, engine)

        html = engine.getHtml()

        if not html:
            logger.error("No HTML to save")
            return False

        filename = self._buildFilename(actionConfig, context)

        outputDir = actionConfig.get(
            "outputDir",
            context.get("outputDir", Path("data/downloads")),
        )

        filepath = Path(outputDir) / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)

            logger.info(f"HTML saved: {filepath}")
            context["savedFile"] = str(filepath)
            return True

        except Exception as e:
            logger.error(
                f"Failed to save HTML: {type(e).__name__}: {e}"
            )
            return False

    def _save_embedded_images(self, actionConfig, context, engine) -> bool:
        """Capture a selected content section and original image bytes atomically.

        The output has stable content: no capture times, filenames or browser
        scripts. A failed image prevents publication of the whole snapshot.
        """
        import copy
        import tempfile
        from html import escape
        from uuid import uuid4

        from lxml import html as lxml_html
        from PIL import Image

        from ingestion.bypassCollector.browserBinaryDownload import fetch_browser_binary
        from ingestion.urlResolver.htmlImageResolver import resolve_image_from_html

        context.pop("savedFile", None)
        part_path = None
        try:
            settings = actionConfig["embed_images"]
            if not isinstance(settings, dict):
                raise ValueError("embed_images must be an object.")
            allowed = {"content_selector", "expected_count", "timeout_seconds"}
            if set(settings) - allowed:
                raise ValueError("Unknown embed_images settings.")
            selector = settings.get("content_selector")
            if not isinstance(selector, str) or not selector.strip():
                raise ValueError("embed_images.content_selector must be a CSS selector.")
            count = settings.get("expected_count")
            timeout = settings.get("timeout_seconds", 30)
            if type(count) is not int or not 1 <= count <= 100:
                raise ValueError("embed_images.expected_count must be between 1 and 100.")
            if type(timeout) is not int or timeout < 1:
                raise ValueError("embed_images.timeout_seconds must be a positive integer.")

            markup = engine.getHtml()
            if not markup:
                raise ValueError("Browser returned empty HTML.")
            page_url = engine.getCurrentUrl() or context.get("url")
            root = lxml_html.fromstring(markup)
            sections = root.cssselect(selector)
            if len(sections) != 1:
                raise ValueError("Content selector must match exactly one section.")
            nodes = sections[0].xpath(".//img")
            if len(nodes) != count:
                raise ValueError(f"Found {len(nodes)} images; expected {count}.")

            assets = []
            for node in nodes:
                selected = resolve_image_from_html(markup, page_url, {
                    "selector_type": "xpath",
                    "selector": root.getroottree().getpath(node),
                    "prefer": "largest_srcset",
                })
                assets.append((selected, " ".join((node.get("alt") or "").split())))
            urls = [selected.url for selected, _ in assets]
            if len(set(urls)) != count:
                raise ValueError("Content contains duplicate image URLs.")

            clean = copy.deepcopy(sections[0])
            for element in clean.xpath(".//script | .//style | .//noscript"):
                element.drop_tree()
            source_text = " ".join(clean.text_content().split())

            options = json.dumps({"selector": selector, "urls": urls,
                                  "timeoutMs": timeout * 1000})
            loaded = engine.evaluateAwait("""(async function(options) {
                const sections = document.querySelectorAll(options.selector);
                if (sections.length !== 1) throw new Error('Content section changed');
                const images = Array.from(sections[0].querySelectorAll('img'));
                if (images.length !== options.urls.length) throw new Error('Image count changed');
                images.forEach((img, index) => {
                    img.removeAttribute('srcset');
                    img.removeAttribute('sizes');
                    img.loading = 'eager';
                    img.src = options.urls[index];
                });
                const deadline = Date.now() + options.timeoutMs;
                while (images.some(img => !img.complete || !img.naturalWidth)) {
                    if (Date.now() >= deadline) throw new Error('Content images did not load');
                    await new Promise(resolve => setTimeout(resolve, 250));
                }
                return images.map(img => ({url: img.currentSrc,
                    width: img.naturalWidth, height: img.naturalHeight}));
            })(""" + options + ")")
            if not isinstance(loaded, list) or len(loaded) != count:
                raise ValueError("Browser did not load every content image.")

            captured = []
            total = 0
            mime_types = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
            for (selected, alt), actual in zip(assets, loaded):
                if not isinstance(actual, dict) or actual.get("url") != selected.url:
                    raise ValueError("Loaded image differs from the selected original.")
                payload, _ = fetch_browser_binary(
                    engine, selected.url, page_url=page_url,
                    timeout_seconds=timeout, cache_mode="force-cache",
                )
                total += len(payload)
                if total > 40_000_000:
                    raise ValueError("Embedded images exceed the 40 MB total limit.")
                with Image.open(BytesIO(payload)) as image:
                    image_format, size = image.format, image.size
                    if image_format not in mime_types or getattr(image, "n_frames", 1) != 1:
                        raise ValueError("Expected a static PNG, JPEG or WEBP image.")
                    if size[0] * size[1] > 40_000_000:
                        raise ValueError("Image exceeds the 40 megapixel limit.")
                    if size != (actual.get("width"), actual.get("height")):
                        raise ValueError("Image dimensions differ from the browser image.")
                    if selected.width_hint is not None and size[0] != selected.width_hint:
                        raise ValueError("Image width differs from the original srcset descriptor.")
                    image.verify()
                with Image.open(BytesIO(payload)) as image:
                    image.load()
                captured.append((selected.url, alt, mime_types[image_format], size, payload))

            title = str(context.get("list_name") or "Source images")
            source_url = str(context.get("url") or page_url)
            lines = ['<!DOCTYPE html>', '<html><head><meta charset="utf-8">',
                     '<meta name="snapshot-format" content="embedded-images-v1">',
                     f'<meta name="source-url" content="{escape(source_url, quote=True)}">',
                     f'<title>{escape(title)}</title></head><body>',
                     f'<h1>{escape(title)}</h1><main data-ocr-images="1">',
                     f'<p data-source-text="1">{escape(source_text)}</p>']
            for index, (url, alt, mime, size, payload) in enumerate(sorted(captured), 1):
                encoded = base64.b64encode(payload).decode("ascii")
                lines.append(
                    f'<figure data-image-index="{index}" data-source-url="{escape(url, quote=True)}">'
                    f'<img alt="{escape(alt, quote=True)}" width="{size[0]}" height="{size[1]}" '
                    f'src="data:{mime};base64,{encoded}"></figure>'
                )
            snapshot = ("\n".join(lines) + "\n</main></body></html>\n").encode("utf-8")

            name = Path(self._buildFilename(actionConfig, context))
            if name.name != str(name) or name.suffix.lower() != ".html":
                raise ValueError("Snapshot filename must be an HTML basename.")
            output_dir = Path(actionConfig.get("outputDir", context.get("outputDir", "data/downloads")))
            output_dir.mkdir(parents=True, exist_ok=True)
            filepath = output_dir / f"{name.stem}_{uuid4().hex[:12]}.html"
            with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".part", delete=False) as handle:
                part_path = Path(handle.name)
                handle.write(snapshot)
            part_path.replace(filepath)
            context["savedFile"] = str(filepath)
            logger.info("Self-contained HTML saved: %s; images=%s; bytes=%s", filepath, count, len(snapshot))
            return True
        except Exception:
            logger.exception("Self-contained HTML capture failed")
            return False
        finally:
            if part_path is not None:
                part_path.unlink(missing_ok=True)

    def _buildFilename(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
    ) -> str:
        pattern = actionConfig.get(
            "filename_pattern",
            "{source}_{list}_{timestamp}.html",
        )

        now = datetime.now()

        variables = {
            "source": context.get("source_name", "UNKNOWN"),
            "list": context.get("list_name", "UNKNOWN"),
            "date": now.strftime("%Y%m%d"),
            "time": now.strftime("%H%M%S"),
            "timestamp": now.strftime("%Y%m%d_%H%M%S"),
        }

        filename = pattern

        for key, value in variables.items():
            placeholder = "{" + key + "}"

            if placeholder in filename:
                filename = filename.replace(
                    placeholder,
                    str(value),
                )

        return filename

    def _save_text(
        self,
        text: str,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
    ) -> bool:
        filename = self._buildFilename(actionConfig, context)

        outputDir = actionConfig.get(
            "outputDir",
            context.get("outputDir", Path("data/downloads")),
        )

        filepath = Path(outputDir) / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(text)

            logger.info(f"HTML saved: {filepath}")
            context["savedFile"] = str(filepath)
            return True

        except Exception as e:
            logger.error(
                f"Failed to save HTML: {type(e).__name__}: {e}"
            )
            return False


class SaveJsonAction(SaveHtmlAction):
    """Fetch JSON using the active browser session."""

    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any,
    ) -> bool:
        url = actionConfig.get("url", "")

        if not url:
            logger.error("save_json requires a 'url'")
            return False

        text = engine.fetchText(url)

        if not text:
            logger.error("No JSON to save")
            return False

        filename = self._buildFilename(actionConfig, context)

        outputDir = actionConfig.get(
            "outputDir",
            context.get("outputDir", Path("data/downloads")),
        )

        filepath = Path(outputDir) / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(text)

            logger.info(f"JSON saved: {filepath}")
            context["savedFile"] = str(filepath)
            return True

        except Exception as e:
            logger.error(
                f"Failed to save JSON: {type(e).__name__}: {e}"
            )
            return False


class SavePaginatedHtmlAction(SaveHtmlAction):
    """Fetch listing pages and combine their HTML bodies."""

    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any,
    ) -> bool:
        base_url = (
            actionConfig.get("url")
            or context.get("url")
        )

        if not base_url:
            logger.error("save_paginated_html requires a 'url'")
            return False

        page_param = actionConfig.get("page_param", "page")
        start_page = int(actionConfig.get("start_page", 0))
        max_pages = int(actionConfig.get("max_pages", 50))

        first_html = engine.getHtml()

        if not first_html:
            logger.error("No HTML to save")
            return False

        bodies = [self._body_inner(first_html)]
        seen_hashes = {self._hash(bodies[0])}

        for page in range(
            start_page + 1,
            start_page + max_pages,
        ):
            page_url = self._with_page(
                base_url,
                page_param,
                page,
            )

            logger.info(f"Fetching listing page: {page_url}")
            html = engine.fetchText(page_url)

            if not html:
                logger.info(
                    f"Listing page {page} returned no content; stopping."
                )
                break

            body = self._body_inner(html)
            digest = self._hash(body)

            if digest in seen_hashes:
                logger.info(
                    f"Listing page {page} repeats an earlier page; stopping."
                )
                break

            seen_hashes.add(digest)
            bodies.append(body)

        combined = (
            "<!DOCTYPE html>\n<html>\n<body>\n"
            + "\n".join(bodies)
            + "\n</body>\n</html>\n"
        )

        logger.info(f"Combined {len(bodies)} listing page(s)")

        return self._save_text(
            combined,
            actionConfig,
            context,
        )

    @staticmethod
    def _body_inner(html: str) -> str:
        match = re.search(
            r"<body[^>]*>(.*)</body>",
            html,
            re.IGNORECASE | re.DOTALL,
        )

        return match.group(1) if match else html

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5(
            text.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _with_page(
        url: str,
        param: str,
        value: int,
    ) -> str:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        query[param] = str(value)

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )


class SavePdfAction(SaveHtmlAction):
    """Download and validate a PDF using the active browser session."""

    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any,
    ) -> bool:
        try:
            url = str(
                actionConfig.get("url") or ""
            ).strip()

            if not url:
                raise ValueError(
                    "save_pdf requires a URL."
                )

            timeout_seconds = int(
                actionConfig.get("timeout_seconds", 60)
            )

            max_bytes = int(
                actionConfig.get(
                    "max_size_bytes",
                    10_000_000,
                )
            )

            if timeout_seconds <= 0 or max_bytes <= 0:
                raise ValueError(
                    "PDF timeout and size limit must be positive."
                )

            options = json.dumps({
                "url": url,
                "timeoutMs": timeout_seconds * 1000,
                "maxBytes": max_bytes,
            })

            expression = """
(async function(options) {
    const controller = new AbortController();

    const timer = setTimeout(
        () => controller.abort(),
        options.timeoutMs
    );

    try {
        const target = new URL(options.url, location.href);

        if (target.origin !== location.origin) {
            throw new Error(
                "PDF must be fetched from the same origin."
            );
        }

        const response = await fetch(target.href, {
            credentials: "same-origin",
            cache: "no-store",
            signal: controller.signal
        });

        const contentType = (
            response.headers.get("content-type") || ""
        );

        if (response.status !== 200) {
            throw new Error(
                "PDF request returned HTTP " + response.status +
                "; Content-Type: " + contentType
            );
        }

        const blob = await response.blob();

        if (!blob.size || blob.size > options.maxBytes) {
            throw new Error(
                "Invalid PDF response size: " + blob.size
            );
        }

        const dataUrl = await new Promise((resolve, reject) => {
            const reader = new FileReader();

            reader.onload = () => resolve(reader.result);

            reader.onerror = () => reject(
                new Error("Cannot read PDF bytes.")
            );

            reader.readAsDataURL(blob);
        });

        return JSON.stringify({
            ok: true,
            status: response.status,
            contentType: contentType,
            size: blob.size,
            base64: dataUrl.slice(dataUrl.indexOf(",") + 1)
        });

    } catch (error) {
        return JSON.stringify({
            ok: false,
            error: String(error.message || error)
        });

    } finally {
        clearTimeout(timer);
    }
})(__OPTIONS__)
""".replace("__OPTIONS__", options)

            if getattr(engine.sb, "cdp", None) is not None:
                result = engine.evaluateAwait(expression)

            else:
                driver = engine.sb.driver
                previous_timeout = driver.timeouts.script

                driver.set_script_timeout(
                    timeout_seconds + 10
                )

                try:
                    result = driver.execute_async_script(
                        "const done = "
                        "arguments[arguments.length - 1];\n"
                        + expression
                        + ".then(done).catch(error => done("
                        + "JSON.stringify({"
                        + "ok: false, error: String(error)"
                        + "})));"
                    )

                finally:
                    driver.set_script_timeout(
                        previous_timeout
                    )

            if isinstance(result, str):
                result = json.loads(result)

            if (
                not isinstance(result, dict)
                or not result.get("ok")
            ):
                error = (
                    result.get("error")
                    if isinstance(result, dict)
                    else (
                        "Browser returned no usable "
                        "download result."
                    )
                )

                raise RuntimeError(error)

            if result.get("status") != 200:
                raise ValueError(
                    "PDF response was not HTTP 200."
                )

            payload = base64.b64decode(
                result["base64"],
                validate=True,
            )

            if (
                not payload
                or len(payload) > max_bytes
                or len(payload) != result.get("size")
            ):
                raise ValueError(
                    "Downloaded PDF size is invalid."
                )

            if not payload.startswith(b"%PDF-"):
                raise ValueError(
                    "Downloaded response is not a PDF."
                )

            with pdfplumber.open(BytesIO(payload)) as pdf:
                if not pdf.pages:
                    raise ValueError(
                        "Downloaded PDF has no pages."
                    )

            file_config = dict(actionConfig)

            file_config.setdefault(
                "filename_pattern",
                "{source}_{list}_{timestamp}.pdf",
            )

            filename = self._buildFilename(
                file_config,
                context,
            )

            output_dir = Path(
                actionConfig.get("outputDir")
                or context.get("outputDir")
                or "data/downloads"
            )

            file_path = output_dir / filename

            file_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            temporary_path = file_path.with_suffix(
                file_path.suffix + ".part"
            )

            try:
                temporary_path.write_bytes(payload)
                temporary_path.replace(file_path)

            finally:
                temporary_path.unlink(missing_ok=True)

            context["savedFile"] = str(file_path)

            logger.info(
                "PDF saved: %s; bytes=%d; Content-Type=%s",
                file_path,
                len(payload),
                result.get("contentType"),
            )

            return True

        except Exception as error:
            logger.error(
                "PDF download failed: %s: %s",
                type(error).__name__,
                error,
            )

            return False
