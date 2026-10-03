"""Unit tests for StealthBrowserEngine's Cloudflare challenge indicators."""

import asyncio
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


ENGINE_TIME = "ingestion.bypassCollector.engines.stealthBrowserEngine.time"


@pytest.mark.parametrize("settle, expected_sleeps", [(None, [5]), (0, [])])
def test_navigate_sleeps_only_when_asked(settle, expected_sleeps):
    engine = StealthBrowserEngine()
    engine._bot = MagicMock()

    with patch(ENGINE_TIME) as fake_time:
        ok = engine.navigate("https://x") if settle is None else engine.navigate("https://x", settle_seconds=settle)

    assert ok
    engine._bot.safe_get.assert_called_once_with("https://x")
    assert [c.args[0] for c in fake_time.sleep.call_args_list] == expected_sleeps


@pytest.mark.parametrize("states, expected", [
    (["loading", "loading", "interactive"], True),
    (["complete"], True),
])
def test_wait_for_page_load_returns_once_scripts_have_run(states, expected):
    engine = StealthBrowserEngine()
    engine.sb = MagicMock()
    engine.sb.execute_script.side_effect = states

    with patch(ENGINE_TIME) as fake_time:
        fake_time.time.return_value = 0
        assert engine.waitForPageLoad(timeout=30) is expected

    assert engine.sb.execute_script.call_count == len(states)


def test_wait_for_page_load_gives_up_after_timeout():
    engine = StealthBrowserEngine()
    engine.sb = MagicMock()
    engine.sb.execute_script.return_value = "loading"

    with patch(ENGINE_TIME) as fake_time:
        fake_time.time.side_effect = [0, 0, 31]
        assert engine.waitForPageLoad(timeout=30) is False


def test_exit_replaces_an_event_loop_closed_by_the_browser():
    closed = asyncio.new_event_loop()
    asyncio.set_event_loop(closed)
    closed.close()
    engine = StealthBrowserEngine()
    engine._bot = MagicMock()

    engine.__exit__(None, None, None)

    loop = asyncio.get_event_loop_policy().get_event_loop()
    assert loop is not closed and not loop.is_closed()
    loop.close()
    asyncio.set_event_loop(None)
