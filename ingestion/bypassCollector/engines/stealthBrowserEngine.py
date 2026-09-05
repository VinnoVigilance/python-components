"""
StealthBot browser engine implementation.
"""

import json
import logging
from typing import Optional, Any
import time

from sb_stealth_wrapper import StealthBot

from ingestion.bypassCollector.engines.baseEngine import BaseEngine
logger = logging.getLogger(__name__)


class StealthBrowserEngine(BaseEngine):
    """
    StealthBot-based browser engine.
    
    Wraps the proven StealthBot implementation for
    Cloudflare bypass.
    """
    
    def __init__(
        self,
        headless: bool = False,
        successCriteria: Optional[list] = None,
        timeoutSeconds: int = 90
    ):
        """
        Initialize StealthBrowserEngine.
        
        Args:
            headless: Run browser in headless mode
            successCriteria: Text indicating successful page load
            timeoutSeconds: Default timeout for operations
        """
        self.headless = headless
        self.successCriteria = successCriteria or []
        self.timeoutSeconds = timeoutSeconds
        self._bot = None
        self.sb = None
    
    def __enter__(self):
        """Initialize StealthBot."""
        logger.info("Initializing StealthBot...")
        
        try:
            # Handle success criteria - convert list to string if needed
            success_criteria = self.successCriteria
            
            if isinstance(success_criteria, list):
                if success_criteria:
                    success_criteria = success_criteria[0]  # Take first item
                else:
                    success_criteria = ""  # Empty string
            elif not success_criteria:
                success_criteria = ""  # None or empty
            
            self._bot = StealthBot(
                headless=self.headless,
                success_criteria=success_criteria
            )
            
            self._bot.__enter__()
            self.sb = self._bot.sb
            
            logger.info("StealthBot initialized successfully")
            return self
        except Exception as e:
            logger.error(f"Failed to initialize StealthBot: {type(e).__name__}: {e}")
            raise
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up StealthBot."""
        if self._bot:
            try:
                self._bot.__exit__(exc_type, exc_val, exc_tb)
                logger.info("StealthBot closed successfully")
            except Exception as e:
                logger.error(f"Error closing StealthBot: {type(e).__name__}: {e}")
    
    def navigate(self, url: str) -> bool:
        """
        Navigate to URL using safe_get.
        
        Args:
            url: Destination URL
            
        Returns:
            True if navigation succeeded
        """
        logger.info(f"Navigating to: {url}")
        
        try:
            self._bot.safe_get(url)
            logger.info("Navigation completed successfully")
            
            # Give page time to initialize
            time.sleep(5)
            
            return True
        except Exception as e:
            logger.error(f"Navigation failed: {type(e).__name__}: {e}")
            return False
    
    def getHtml(self) -> Optional[str]:
        """
        Get current DOM HTML.
        
        Returns:
            Full rendered HTML string or None
        """
        logger.info("Retrieving DOM HTML...")
        
        try:
            html = self.sb.execute_script(
                "return document.documentElement.outerHTML;"
            )
            
            if html:
                logger.info(f"Retrieved {len(html):,} characters")
                return html
            else:
                logger.info("Retrieved empty HTML")
                return None
        except Exception as e:
            logger.error(f"Failed to retrieve HTML: {type(e).__name__}: {e}")
            return None

    def fetchText(self, url: str) -> Optional[str]:
        """
        Fetch a URL's raw body from inside the cleared browser session.

        Runs a same-origin XHR in the page, so the Cloudflare clearance
        cookies set during navigate() are sent automatically. This is the
        "bypass then API" step: the challenge is already cleared, so we pull
        the JSON straight from the endpoint instead of reading the <pre> the
        browser wraps around a rendered .json page.
        """
        logger.info(f"Fetching body via browser session: {url}")

        try:
            # UC/CDP mode evaluates the script as an expression, so a top-level
            # `return` is illegal and script arguments are not passed. Wrap the
            # synchronous XHR in an IIFE (an expression) with the URL embedded.
            script = (
                "(function(u){"
                "var xhr = new XMLHttpRequest();"
                "xhr.open('GET', u, false);"
                "xhr.send(null);"
                "return xhr.responseText;"
                "})(" + json.dumps(url) + ")"
            )
            text = self.sb.execute_script(script)

            if text:
                logger.info(f"Fetched {len(text):,} characters")
                return text

            logger.error("Fetched empty response")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch body: {type(e).__name__}: {e}")
            return None

    def getPageTitle(self) -> Optional[str]:
        """Get current page title."""
        try:
            return self.sb.get_page_title()
        except Exception as e:
            logger.error(f"Failed to get page title: {type(e).__name__}: {e}")
            return None
    
    def getCurrentUrl(self) -> Optional[str]:
        """Get current URL."""
        try:
            return self.sb.get_current_url()
        except Exception as e:
            logger.error(f"Failed to get current URL: {type(e).__name__}: {e}")
            return None
    
    def isElementPresent(self, selector: str) -> bool:
        """
        Check if element exists.
        
        Args:
            selector: CSS selector
            
        Returns:
            True if element is present
        """
        try:
            return self.sb.is_element_present(selector)
        except Exception:
            return False
    
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
        logger.info(f"Waiting for element: {selector}")
        
        start = time.time()
        
        while time.time() - start < timeout:
            try:
                if self.sb.is_element_present(selector):
                    logger.info(f"Element found: {selector}")
                    return True
            except Exception:
                pass
            
            time.sleep(2)
            elapsed = int(time.time() - start)
            logger.warning(f"Still waiting... {elapsed}/{timeout}s")
        
        logger.error(f"Element not found after {timeout}s: {selector}")
        return False
    
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
        logger.info(f"Waiting for text: {text}")
        
        start = time.time()
        
        while time.time() - start < timeout:
            html = self.getHtml()
            
            if html and text.lower() in html.lower():
                logger.info(f"Text found: {text}")
                return True
            
            time.sleep(2)
            elapsed = int(time.time() - start)
            logger.warning(f"Still waiting... {elapsed}/{timeout}s")
        
        logger.error(f"Text not found after {timeout}s: {text}")
        return False
    
    def saveScreenshot(self, filepath: str) -> bool:
        """
        Save screenshot of current page.
        
        Args:
            filepath: Where to save screenshot
            
        Returns:
            True if saved successfully
        """
        try:
            self.sb.save_screenshot(filepath)
            logger.info(f"Screenshot saved: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Screenshot failed: {type(e).__name__}: {e}")
            return False
    
    def executeScript(self, script: str, *args) -> Any:
        """
        Execute JavaScript.
        
        Args:
            script: JavaScript code to execute
            *args: Arguments for script
            
        Returns:
            Script result
        """
        try:
            return self.sb.execute_script(script, *args)
        except Exception as e:
            logger.error(f"Script execution failed: {type(e).__name__}: {e}")
            return None