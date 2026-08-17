"""
Wait actions.
"""

import logging
import time
from typing import Dict, Any

from ingestion.bypassCollector.actions.baseAction import BaseAction
logger = logging.getLogger(__name__)


class WaitAction(BaseAction):
    """
    Wait for content to appear.
    
    Config:
        type: "text" | "selector" | "time"
        text: Text to wait for (if type=text)
        selector: CSS selector (if type=selector)
        seconds: Time to wait (if type=time)
        timeout: Max seconds to wait (for text/selector)
    """
    
    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any
    ) -> bool:
        """Execute wait action."""
        
        waitType = actionConfig.get("type", "time")
        
        if waitType == "text":
            text = actionConfig.get("text", "")
            timeout = actionConfig.get("timeout", 60)
            
            if not text:
                logger.error("No text specified for wait")
                return False
            
            return engine.waitForText(text, timeout)
        
        elif waitType == "selector":
            selector = actionConfig.get("selector", "")
            timeout = actionConfig.get("timeout", 60)
            
            if not selector:
                logger.error("No selector specified for wait")
                return False
            
            return engine.waitForElement(selector, timeout)
        
        elif waitType == "time":
            seconds = actionConfig.get("seconds", 5)
            logger.info(f"Waiting {seconds} seconds...")
            time.sleep(seconds)
            return True
        
        else:
            logger.error(f"Unknown wait type: {waitType}")
            return False