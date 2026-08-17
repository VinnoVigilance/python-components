"""
Navigation actions.
"""

import logging
from typing import Dict, Any

from ingestion.bypassCollector.actions.baseAction import BaseAction
logger = logging.getLogger(__name__)


class NavigateAction(BaseAction):
    """
    Navigate to a URL.
    
    Config:
        url: Destination URL (supports {variable} substitution)
    """
    
    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any
    ) -> bool:
        """Navigate to URL."""
        
        url = actionConfig.get("url", "")
        
        # Substitute variables from context
        url = self._substituteVariables(url, context)
        
        if not url:
            logger.error("No URL specified for navigation")
            return False
        
        return engine.navigate(url)
    
    def _substituteVariables(
        self,
        text: str,
        context: Dict[str, Any]
    ) -> str:
        """Replace {variable} with values from context."""
        
        for key, value in context.items():
            placeholder = "{" + key + "}"
            if placeholder in text:
                text = text.replace(placeholder, str(value))
        
        return text