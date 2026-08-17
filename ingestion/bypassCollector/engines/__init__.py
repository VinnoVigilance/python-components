"""
Browser engines for bypass collection.
"""

from ingestion.bypassCollector.engines.baseEngine import BaseEngine
from ingestion.bypassCollector.engines.stealthBrowserEngine import StealthBrowserEngine

__all__ = ["BaseEngine", "StealthBrowserEngine"]