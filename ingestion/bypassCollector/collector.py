"""
Main BypassCollector orchestrator.
"""

import logging
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from ingestion.bypassCollector.engines.stealthBrowserEngine import StealthBrowserEngine
from ingestion.bypassCollector.actionExecutor import ActionExecutor
logger = logging.getLogger(__name__)


class BypassCollector:
    """
    Collects raw artifacts from protected sources.
    
    Flow:
    1. Initialize engine (chosen from the declared challenge)
    2. Navigate to URL (the engine clears the challenge itself)
    3. Execute collection actions
    4. Validate artifact
    5. Return saved file path
    
    Usage:
        config = {
            "bypass_config": {
                "challenge": "cloudflare",
                "headless": False,
                "actions": [...]
            }
        }
        
        collector = BypassCollector()
        filepath = collector.collect(config)
    """

    # Which engine clears which challenge. The config declares the
    # challenge (e.g. "cloudflare") and the collector picks the engine that
    # handles it -- the challenge is never detected from the page at runtime.
    CHALLENGE_ENGINES = {
        "cloudflare": "stealth_browser",
    }

    def __init__(
        self,
        outputDir: Path = Path("data/downloads")
    ):
        """
        Initialize BypassCollector.

        Args:
            outputDir: Base directory for saved artifacts
        """
        self.outputDir = outputDir
        self.actionExecutor = ActionExecutor()
    
    def collect(self, config: Dict[str, Any]) -> Optional[Path]:
        """
        Collect artifact from protected source.
        
        Args:
            config: Full source config from WATCHLIST_CONFIGS
            
        Returns:
            Path to saved artifact or None if failed
        """
        
        bypassConfig = config.get("bypass_config", {})
        
        # Build context
        context = {
            "source_name": config.get("source_name", "UNKNOWN"),
            "list_name": config.get("list_name", "UNKNOWN"),
            "url": config.get("url"),
            "outputDir": self._buildOutputDir(config)
        }
        
        # Initialize engine
        engine = self._createEngine(bypassConfig)
        
        if not engine:
            logger.error("Failed to create engine")
            return None
        
        try:
            with engine:
                # Navigate to URL
                url = config.get("url")
                
                if url:
                    if not engine.navigate(url):
                        return None
                else:
                    logger.error("No URL in config")
                    return None

                # No challenge detection here: the engine chosen for the
                # declared challenge (e.g. the stealth browser for
                # "cloudflare") clears it during navigate().

                # Execute actions
                actions = bypassConfig.get("actions", [])
                
                if not actions:
                    logger.error("No actions defined in bypass_config")
                    return None
                
                success = self.actionExecutor.executeActions(
                    actions,
                    context,
                    engine
                )
                
                if not success:
                    logger.error("Actions failed")
                    return None
                
                # Validate if configured
                if self._validateArtifact(
                    context,
                    bypassConfig
                ):
                    logger.info("Collection successful")
                    savedFile = context.get("savedFile")
                    
                    if savedFile:
                        return Path(savedFile)
                
                return None
        except Exception as e:
            logger.error(f"Collection failed: {type(e).__name__}: {e}")
            return None
        finally:
            # Always tidy up after the browser, even on failure. Runs after
            # the engine has closed (the `with` block above has exited).
            self._cleanupBrowserArtifacts()

    def _cleanupBrowserArtifacts(self) -> None:
        """
        Remove the browser engine's scratch download folder.

        The stealth engine (SeleniumBase underneath) creates a
        ``downloaded_files/`` folder + driver lock files in the working
        directory as a side effect of driving the browser. Our real artifact
        is saved under data/downloads by the save_html action, so this scratch
        folder is never our output and is safe to delete after every run --
        important on a long-lived server where it would otherwise accumulate.
        """
        scratch = Path("downloaded_files")

        if not scratch.exists():
            return

        try:
            shutil.rmtree(scratch)
            logger.info(f"Cleaned up browser scratch folder: {scratch}")
        except Exception as e:
            logger.warning(
                f"Could not remove {scratch}: {type(e).__name__}: {e}"
            )

    def _createEngine(
        self,
        bypassConfig: Dict[str, Any]
    ) -> Optional[Any]:
        """
        Create browser engine based on config.
        
        Args:
            bypassConfig: Engine configuration
            
        Returns:
            Engine instance or None
        """
        
        # The engine is chosen from the declared challenge (e.g.
        # "cloudflare" -> stealth browser). An explicit "engine" key still
        # wins, for a source that ever needs to force a specific engine.
        engineType = bypassConfig.get("engine")

        if not engineType:
            challenge = bypassConfig.get("challenge")
            engineType = self.CHALLENGE_ENGINES.get(challenge)

        if not engineType:
            logger.error(
                "No engine for challenge "
                f"{bypassConfig.get('challenge')!r}; set a known 'challenge' "
                "or an explicit 'engine' in bypass_config."
            )
            return None

        if engineType == "stealth_browser":
            return StealthBrowserEngine(
                headless=bypassConfig.get("headless", False),
                successCriteria=bypassConfig.get(
                    "success_criteria",
                    []
                ),
                timeoutSeconds=bypassConfig.get(
                    "timeout_seconds",
                    90
                )
            )
        
        elif engineType == "playwright":
            # Future: Playwright engine
            logger.warning("Playwright engine not yet implemented")
            return None
        
        else:
            logger.error(f"Unknown engine: {engineType}")
            return None
    
    def _buildOutputDir(
        self,
        config: Dict[str, Any]
    ) -> Path:
        """
        Build output directory based on source config.
        
        Structure:
        data/downloads/{source}/{list}/year={YYYY}/month={MM}/day={DD}/
        """
        
        sourceName = config.get("source_name", "UNKNOWN")
        listName = config.get("list_name", "UNKNOWN")
        
        now = datetime.now()
        
        outputDir = (
            self.outputDir
            / sourceName
            / listName
            / f"year={now.year}"
            / f"month={now.month:02d}"
            / f"day={now.day:02d}"
        )
        
        outputDir.mkdir(parents=True, exist_ok=True)
        
        return outputDir
    
    def _validateArtifact(
        self,
        context: Dict[str, Any],
        bypassConfig: Dict[str, Any]
    ) -> bool:
        """
        Validate collected artifact.
        
        Args:
            context: Collection context
            bypassConfig: Validation config
            
        Returns:
            True if validation passed
        """
        
        validation = bypassConfig.get("validation")
        
        if not validation:
            return True  # No validation configured
        
        savedFile = context.get("savedFile")
        
        if not savedFile:
            logger.error("No saved file to validate")
            return False
        
        filepath = Path(savedFile)
        
        if not filepath.exists():
            logger.error(f"File not found: {filepath}")
            return False
        
        # Size validation
        minSize = validation.get("min_size_bytes")
        
        if minSize:
            size = filepath.stat().st_size
            
            if size < minSize:
                logger.info(
                    f"File too small: {size} bytes "
                    f"(minimum: {minSize})"
                )
                return False
        
        # Content validation
        requiredContent = validation.get("required_content", [])
        
        if requiredContent:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    html = f.read()
                
                htmlLower = html.lower()
                
                for content in requiredContent:
                    if content.lower() not in htmlLower:
                        logger.info(
                            f"Missing content: {content}"
                        )
                        return False
            except Exception as e:
                logger.error(f"Could not read file: {type(e).__name__}: {e}")
                return False
        
        logger.info("Validation passed")
        return True