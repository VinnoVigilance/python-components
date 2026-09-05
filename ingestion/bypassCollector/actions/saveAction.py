"""
Saving actions.
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

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