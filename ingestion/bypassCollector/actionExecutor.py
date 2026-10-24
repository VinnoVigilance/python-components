"""
Action executor for bypass collection.
"""

import logging
from typing import Any, Dict, List

from ingestion.bypassCollector.actions.navigateAction import NavigateAction
from ingestion.bypassCollector.actions.waitAction import WaitAction
from ingestion.bypassCollector.actions.saveAction import (
    SaveHtmlAction,
    SaveJsonAction,
    SavePdfAction,
    SavePaginatedHtmlAction,
)
from ingestion.bypassCollector.actions.executeJsAction import ExecuteJsAction
from ingestion.bypassCollector.actions.collectPopupProfilesAction import (
    CollectPopupProfilesAction,
)


logger = logging.getLogger(__name__)


class ActionExecutor:
    """Execute collection actions sequentially with a shared context."""

    def __init__(self):
        self._actions = {
            "navigate": NavigateAction(),
            "wait": WaitAction(),
            "save_html": SaveHtmlAction(),
            "save_json": SaveJsonAction(),
            "save_pdf": SavePdfAction(),
            "save_paginated_html": SavePaginatedHtmlAction(),
            "execute_js": ExecuteJsAction(),
            "collect_popup_profiles": CollectPopupProfilesAction(),

        }

    def executeActions(
        self,
        actions: List[Dict[str, Any]],
        context: Dict[str, Any],
        engine: Any,
    ) -> bool:
        for i, actionConfig in enumerate(actions):
            actionType = actionConfig.get("action")

            if not actionType:
                logger.error(
                    f"Action missing type at index {i}"
                )
                return False

            action = self._actions.get(actionType)

            if not action:
                logger.error(
                    f"Unknown action: {actionType}"
                )
                return False

            logger.info(f"{'=' * 60}")
            logger.info(f"Action {i + 1}: {actionType}")
            logger.info(f"{'=' * 60}")

            success = action.execute(
                actionConfig,
                context,
                engine,
            )

            if not success:
                logger.error(
                    f"Action failed: {actionType}"
                )
                return False

        return True