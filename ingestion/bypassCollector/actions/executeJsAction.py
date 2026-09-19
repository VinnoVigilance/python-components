"""JavaScript execution action."""

import logging
from typing import Any, Dict

from ingestion.bypassCollector.actions.baseAction import BaseAction

logger = logging.getLogger(__name__)


class ExecuteJsAction(BaseAction):
    """Run a JavaScript snippet in the page to drive dynamic listings.

    Config:
        script: JavaScript to run.
        await: when true, evaluate a Promise-returning expression and wait
            for it (e.g. select "show all" and resolve once the list is loaded).
    """

    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any,
    ) -> bool:
        script = actionConfig.get("script")

        if not script:
            logger.error("execute_js requires a 'script'")
            return False

        if actionConfig.get("await", False):
            engine.evaluateAwait(script)
        else:
            engine.executeScript(script)

        return True
