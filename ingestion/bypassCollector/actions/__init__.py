"""
Collection actions for bypass collector.
"""

from ingestion.bypassCollector.actions.navigateAction import NavigateAction
from ingestion.bypassCollector.actions.waitAction import WaitAction
from ingestion.bypassCollector.actions.saveAction import SaveHtmlAction, SaveJsonAction

__all__ = ["NavigateAction", "WaitAction", "SaveHtmlAction", "SaveJsonAction"]