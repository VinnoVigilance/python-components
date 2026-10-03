from unittest.mock import MagicMock, call, patch

import pytest

from ingestion.crawler.browserDetailFetcher import (
    PAGE_GAP_SECONDS,
    BrowserDetailFetcher,
)


pytestmark = pytest.mark.unit


def test_detail_failure_does_not_block_remaining_items():
    first_engine = MagicMock()
    first_engine.navigate.side_effect = [
        True,
        False,
    ]
    first_engine.getHtml.return_value = (
        "<html>first</html>"
    )

    second_engine = MagicMock()
    second_engine.navigate.return_value = True
    second_engine.getHtml.return_value = (
        "<html>third</html>"
    )

    engine_factory = MagicMock(
        side_effect=[
            first_engine,
            second_engine,
        ]
    )

    fetcher = BrowserDetailFetcher(
        browser_config={},
        storage_config={
            "reuse_saved_detail_pages": False,
        },
        engine_factory=engine_factory,
    )

    results = list(
        fetcher.fetch(
            [
                {"detail_url": "https://x/1"},
                {"detail_url": "https://x/2"},
                {"detail_url": "https://x/3"},
            ]
        )
    )

    assert len(results) == 3
    assert results[0][1] is not None
    assert results[1][1] is None
    assert "Could not open detail page" in (
        results[1][0]["fetch_error"]
    )
    assert results[2][1] is not None

    # A failed browser session is discarded and the
    # next item starts in a fresh session.
    assert engine_factory.call_count == 2
    first_engine.__exit__.assert_called_once_with(
        None,
        None,
        None,
    )
    second_engine.__exit__.assert_called_once_with(
        None,
        None,
        None,
    )


def _fetch_three(browser_config):
    engine = MagicMock()
    engine.navigate.return_value = True
    engine.waitForElement.return_value = True
    engine.getHtml.return_value = "<html>page</html>"

    fetcher = BrowserDetailFetcher(
        browser_config=browser_config,
        storage_config={"reuse_saved_detail_pages": False},
        engine_factory=MagicMock(return_value=engine),
    )

    with patch("ingestion.crawler.browserDetailFetcher.time") as fake_time:
        results = list(
            fetcher.fetch(
                [{"detail_url": f"https://x/{n}"} for n in (1, 2, 3)]
            )
        )

    return engine, fake_time, results


def test_wait_selector_skips_fixed_sleep_and_paces_between_pages():
    engine, fake_time, results = _fetch_three({"wait_selector": ".body"})

    assert all(response is not None for _, response in results)
    engine.navigate.assert_has_calls(
        [call(f"https://x/{n}", settle_seconds=0) for n in (1, 2, 3)]
    )
    assert engine.waitForPageLoad.call_count == 3

    gaps = [c.args[0] for c in fake_time.sleep.call_args_list]
    assert len(gaps) == 2
    assert all(PAGE_GAP_SECONDS[0] <= gap <= PAGE_GAP_SECONDS[1] for gap in gaps)


def test_without_wait_selector_keeps_fixed_settle_and_no_gap():
    engine, fake_time, _ = _fetch_three({})

    engine.navigate.assert_has_calls(
        [call(f"https://x/{n}", settle_seconds=5) for n in (1, 2, 3)]
    )
    engine.waitForElement.assert_not_called()
    engine.waitForPageLoad.assert_not_called()
    fake_time.sleep.assert_not_called()
