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
    """Save the current browser DOM as HTML."""

    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any,
    ) -> bool:
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