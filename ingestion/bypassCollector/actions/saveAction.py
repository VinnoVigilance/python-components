"""
Saving actions.
"""

import hashlib
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ingestion.bypassCollector.actions.baseAction import BaseAction
logger = logging.getLogger(__name__)


class SaveHtmlAction(BaseAction):
    """
    Save current DOM HTML to file.
    
    Config:
        filename_pattern: Pattern for filename with variables
            Available: {source}, {list}, {date}, {time}, {timestamp}
        outputDir: Optional output directory override
    
    Context updates:
        savedFile: Path to saved file
    """
    
    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any
    ) -> bool:
        """Save HTML to file."""
        
        # Get HTML
        html = engine.getHtml()
        
        if not html:
            logger.error("No HTML to save")
            return False
        
        # Build filename
        filename = self._buildFilename(actionConfig, context)
        
        # Determine output directory
        outputDir = actionConfig.get(
            "outputDir",
            context.get("outputDir", Path("data/downloads"))
        )
        
        filepath = Path(outputDir) / filename
        
        # Create parent directories
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        # Save HTML
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
            
            logger.info(f"HTML saved: {filepath}")
            
            # Store in context
            context["savedFile"] = str(filepath)
            
            return True
        except Exception as e:
            logger.error(f"Failed to save HTML: {type(e).__name__}: {e}")
            return False
    
    def _buildFilename(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any]
    ) -> str:
        """Build filename from pattern."""
        
        pattern = actionConfig.get(
            "filename_pattern",
            "{source}_{list}_{timestamp}.html"
        )
        
        now = datetime.now()
        
        variables = {
            "source": context.get("source_name", "UNKNOWN"),
            "list": context.get("list_name", "UNKNOWN"),
            "date": now.strftime("%Y%m%d"),
            "time": now.strftime("%H%M%S"),
            "timestamp": now.strftime("%Y%m%d_%H%M%S")
        }
        
        filename = pattern

        for key, value in variables.items():
            placeholder = "{" + key + "}"
            if placeholder in filename:
                filename = filename.replace(placeholder, str(value))

        return filename

    def _save_text(
        self,
        text: str,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any]
    ) -> bool:
        """Write text to the configured file and record it in context."""

        filename = self._buildFilename(actionConfig, context)

        outputDir = actionConfig.get(
            "outputDir",
            context.get("outputDir", Path("data/downloads"))
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
            logger.error(f"Failed to save HTML: {type(e).__name__}: {e}")
            return False


class SaveJsonAction(SaveHtmlAction):
    """
    Fetch a JSON endpoint from inside the cleared browser session and save
    the raw response body to file.

    Unlike ``save_html`` (which stores the rendered DOM), this pulls the raw
    JSON via the browser's XHR, so a .json endpoint is saved as clean JSON
    rather than HTML-wrapped text.

    Config:
        url: JSON endpoint to fetch (uses the browser's cleared session/cookies)
        filename_pattern: Pattern for filename (e.g. "{source}_{list}_{timestamp}.json")
        outputDir: Optional output directory override

    Context updates:
        savedFile: Path to saved file
    """

    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any
    ) -> bool:
        """Fetch the JSON body and save it."""

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
            context.get("outputDir", Path("data/downloads"))
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
            logger.error(f"Failed to save JSON: {type(e).__name__}: {e}")
            return False


class SavePaginatedHtmlAction(SaveHtmlAction):
    """Walk a paginated listing and save every page combined into one HTML file."""

    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any
    ) -> bool:
        """Fetch every listing page and save them combined into one file."""

        base_url = actionConfig.get("url") or context.get("url")

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

        for page in range(start_page + 1, start_page + max_pages):
            page_url = self._with_page(base_url, page_param, page)
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

        return self._save_text(combined, actionConfig, context)

    @staticmethod
    def _body_inner(html: str) -> str:
        """Return the inner HTML of <body>, or the whole document if absent."""
        match = re.search(
            r"<body[^>]*>(.*)</body>",
            html,
            re.IGNORECASE | re.DOTALL,
        )

        return match.group(1) if match else html

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _with_page(url: str, param: str, value: int) -> str:
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