"""
Device-compatible driver strategy for the stealth browser.

The default strategy in ``sb_stealth_wrapper`` launches SeleniumBase with no
driver-version pin, so SeleniumBase downloads the *global* "Latest Stable"
chromedriver. On any machine whose installed Chrome is not that exact latest
version, the driver and the browser cannot talk to each other -- Chrome never
starts and the launch fails with a "connection refused" error. Because "Latest
Stable" moves every time Google ships a new Chrome, this breaks unpredictably
across devices and over time.

This strategy fixes that at the root by pinning ``driver_version="mlatest"``
("matching latest"): SeleniumBase reads the Chrome major version installed *on
this device* and fetches the chromedriver that matches it. So the same code runs
on any machine that has Chrome, regardless of which Chrome version that is.

It plugs into StealthBot through its supported ``driver_strategy`` injection
point, so the installed package is never modified (a machine-local edit would
not travel to other devices anyway).
"""

import logging
import platform
from typing import Any, Optional

from seleniumbase import SB
from sb_stealth_wrapper import SeleniumBaseDriver

logger = logging.getLogger(__name__)


class CompatibleSeleniumBaseDriver(SeleniumBaseDriver):
    """
    SeleniumBase driver strategy that matches the chromedriver to the Chrome
    already installed on the current device.

    Args:
        driver_version: SeleniumBase driver-version selector. Default
            ``"mlatest"`` = the latest chromedriver for the *installed* Chrome
            major version. Override only to force a specific build (e.g. an exact
            ``"152.0.7977.64"``, or ``"keep"`` to reuse a cached driver offline).
        binary_location: explicit path to a Chrome/Chromium binary. Leave None
            to let SeleniumBase auto-detect the system Chrome.
    """

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
        # Mirror the default strategy's Linux handling: on Linux/CI, run headed
        # under a virtual display (Xvfb) rather than headless, which the bot
        # wall detects more easily.
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
            # The one line that makes this device-agnostic: fetch the driver
            # that matches THIS machine's Chrome, not the global latest.
            driver_version=self.driver_version,
        )

        if self.binary_location:
            sb_kwargs["binary_location"] = self.binary_location

        logger.info(
            f"Launching Chrome with driver_version={self.driver_version!r} "
            f"(matches the browser installed on this device)."
        )

        self._sb_context = SB(**sb_kwargs)
        self.sb = self._sb_context.__enter__()

        return self.sb
