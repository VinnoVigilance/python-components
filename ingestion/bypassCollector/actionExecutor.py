"""
Action executor for bypass collection.
"""

import logging
from typing import Dict, Any, List

from ingestion.bypassCollector.actions.navigateAction import NavigateAction
from ingestion.bypassCollector.actions.waitAction import WaitAction
from ingestion.bypassCollector.actions.saveAction import SaveHtmlAction, SaveJsonAction
logger = logging.getLogger(__name__)


class ActionExecutor:
    """
    Executes a list of collection actions.
    
    Actions are executed sequentially, each receiving
    the shared context.
    """
    
    def __init__(self):
        """Initialize executor with registered actions."""
        
        self._actions = {
            "navigate": NavigateAction(),
            "wait": WaitAction(),
            "save_html": SaveHtmlAction(),
            "save_json": SaveJsonAction(),
            # Future actions:
            # "click": ClickAction(),
            # "scroll": ScrollAction(),
            # "extract_links": ExtractLinksAction(),
            # "for_each": ForEachAction(),
            # etc.
        }
    
    def executeActions(
        self,
        actions: List[Dict[str, Any]],
        context: Dict[str, Any],
        engine: Any
    ) -> bool:
        """
        Execute a list of actions.
        
        Args:
            actions: List of action configs
            context: Shared context
            engine: Browser engine
            
        Returns:
            True if all actions succeeded
        """
        
        for i, actionConfig in enumerate(actions):
            actionType = actionConfig.get("action")
            
            if not actionType:
                logger.error(f"Action missing type at index {i}")
                return False
            
            action = self._actions.get(actionType)
            
            if not action:
                logger.error(f"Unknown action: {actionType}")
                return False
            
            logger.info(f"{'=' * 60}")
            logger.info(f"Action {i + 1}: {actionType}")
            logger.info(f"{'=' * 60}")
            
            success = action.execute(
                actionConfig,
                context,
                engine
            )
            
            if not success:
                logger.error(f"Action failed: {actionType}")
                return False
        
        return True