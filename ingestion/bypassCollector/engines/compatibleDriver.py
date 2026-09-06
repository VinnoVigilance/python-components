"""SeleniumBase driver strategy that matches the chromedriver to the Chrome
installed on this device (``driver_version="mlatest"``), so the same code runs on
any machine. Plugs in via StealthBot's ``driver_strategy`` injection point."""

import logging
import platform
from typing import Any, Optional

from seleniumbase import SB
from sb_stealth_wrapper import SeleniumBaseDriver

logger = logging.getLogger(__name__)


class CompatibleSeleniumBaseDriver(SeleniumBaseDriver):
    """Driver strategy pinned to the device's installed Chrome."""

    def __init__(
        self,
        driver_version: str = "mlatest",
        binary_location: Optional[str] = None,
    ):
        super().__init__()
        self.driver_version = driver_version
        self.binary_location = binary_location

    def initialize(
        self,
        headless: bool = False,
        proxy: Optional[str] = None,
    ) -> Any:
        is_linux = platform.system() == "Linux"
        xvfb = False

        if is_linux:
            logger.info("Linux/CI detected: using Xvfb with headed mode.")
            xvfb = True
            headless = False

        sb_kwargs = dict(
            uc=True,
            headless=headless,
            xvfb=xvfb,
            proxy=proxy,
            test=False,
            rtf=False,
            driver_version=self.driver_version,
        )

        if self.binary_location:
            sb_kwargs["binary_location"] = self.binary_location

        logger.info(
            f"Launching Chrome with driver_version={self.driver_version!r}."
        )

        self._sb_context = SB(**sb_kwargs)
        self.sb = self._sb_context.__enter__()

        return self.sb
