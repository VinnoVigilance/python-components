"""
Base action interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseAction(ABC):
    """
    Abstract base class for collection actions.
    
    Each action does one specific thing during collection.
    """
    
    @abstractmethod
    def execute(
        self,
        actionConfig: Dict[str, Any],
        context: Dict[str, Any],
        engine: Any
    ) -> bool:
        """
        Execute the action.
        
        Args:
            actionConfig: Action-specific configuration
            context: Shared context (variables, state)
            engine: Browser engine to use
            
        Returns:
            True if action succeeded
        """
        pass