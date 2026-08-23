"""
Base engine interface for bypass collectors.
"""

from abc import ABC, abstractmethod
from typing import Optional, Any


class BaseEngine(ABC):
    """
    Abstract base class for browser engines.
    
    Any engine (StealthBot, Playwright, etc.) must implement
    these methods to be usable by the BypassCollector.
    """
    
    @abstractmethod
    def __enter__(self):
        """Initialize engine resources."""
        pass
    
    @abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up engine resources."""
        pass
    
    @abstractmethod
    def navigate(self, url: str) -> bool:
        """
        Navigate to a URL.
        
        Args:
            url: Destination URL
            
        Returns:
            True if navigation succeeded
        """
        pass
    
    @abstractmethod
    def getHtml(self) -> Optional[str]:
        """
        Get current DOM HTML.
        
        Returns:
            Full rendered HTML string or None
        """
        pass
    
    @abstractmethod
    def getPageTitle(self) -> Optional[str]:
        """Get current page title."""
        pass
    
    @abstractmethod
    def getCurrentUrl(self) -> Optional[str]:
        """Get current URL."""
        pass
    
    @abstractmethod
    def isElementPresent(self, selector: str) -> bool:
        """
        Check if element exists in DOM.
        
        Args:
            selector: CSS selector
            
        Returns:
            True if element is present
        """
        pass
    
    @abstractmethod
    def waitForElement(
        self,
        selector: str,
        timeout: int = 60
    ) -> bool:
        """
        Wait for element to appear.
        
        Args:
            selector: CSS selector to wait for
            timeout: Maximum seconds to wait
            
        Returns:
            True if element appeared
        """
        pass
    
    @abstractmethod
    def waitForText(
        self,
        text: str,
        timeout: int = 60
    ) -> bool:
        """
        Wait for text to appear in page.
        
        Args:
            text: Text to search for
            timeout: Maximum seconds to wait
            
        Returns:
            True if text appeared
        """
        pass
    
    @abstractmethod
    def saveScreenshot(self, filepath: str) -> bool:
        """
        Save screenshot of current page.
        
        Args:
            filepath: Where to save screenshot
            
        Returns:
            True if saved successfully
        """
        pass
    
    @abstractmethod
    def executeScript(self, script: str, *args) -> Any:
        """
        Execute JavaScript in browser.
        
        Args:
            script: JavaScript code to execute
            *args: Arguments for script
            
        Returns:
            Script result
        """
        pass