"""Unit tests for StealthBrowserEngine's Cloudflare challenge indicators."""

from unittest.mock import MagicMock, patch

import pytest

from ingestion.bypassCollector.engines.stealthBrowserEngine import (
    CHALLENGE_INDICATORS,
    StealthBrowserEngine,
)


pytestmark = pytest.mark.unit

CLOUDFLARE_BEACON = "<script>a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';</script>"


def _is_challenge(page_source):
    return any(indicator in page_source.lower() for indicator in CHALLENGE_INDICATORS)


def test_cleared_page_with_cloudflare_beacon_is_not_a_challenge():
    assert not _is_challenge(f"<html><head>{CLOUDFLARE_BEACON}</head><body>Case Summaries</body></html>")


@pytest.mark.parametrize("page", [
    "<title>Just a moment...</title>",
    '<div class="cf-turnstile"></div>',
    "<p>Verify you are human by completing the action below.</p>",
])
def test_real_challenge_page_is_still_detected(page):
    assert _is_challenge(page)


def test_engine_installs_indicators_on_the_bot():
    bot = MagicMock()

    with patch(
        "ingestion.bypassCollector.engines.stealthBrowserEngine.StealthBot",
        return_value=bot,
    ), patch(
        "ingestion.bypassCollector.engines.stealthBrowserEngine.CompatibleSeleniumBaseDriver",
    ):
        StealthBrowserEngine().__enter__()

    assert bot.CHALLENGE_INDICATORS == CHALLENGE_INDICATORS
    bot.__enter__.assert_called_once()
