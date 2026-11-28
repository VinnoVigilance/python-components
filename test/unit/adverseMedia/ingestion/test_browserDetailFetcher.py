from unittest.mock import MagicMock, call, patch

import pytest

from ingestion.crawler.browserDetailFetcher import (
    PAGE_GAP_SECONDS,
    BrowserDetailFetcher,
)


pytestmark = pytest.mark.unit


def test_binary_download_reuses_open_browser_and_existing_transport():
    engine = MagicMock()
    fetcher = BrowserDetailFetcher(browser_config={})
    fetcher._engine = engine
    with patch("ingestion.bypassCollector.browserBinaryDownload.fetch_browser_binary",
               return_value=(b"%PDF-original", {})) as transport:
        payload = fetcher.download_binary("https://court.example/order.pdf",
                                          page_url="https://court.example/cases/",
                                          timeout_seconds=90, max_bytes=1000)
    assert payload == b"%PDF-original"
    transport.assert_called_once_with(engine, "https://court.example/order.pdf",
                                      page_url="https://court.example/cases/",
                                      timeout_seconds=90, max_bytes=1000)


@pytest.mark.parametrize("mode", ["safe_get", "uc_open"])
def test_navigation_mode_reaches_existing_engine(mode):
    factory = MagicMock()
    fetcher = BrowserDetailFetcher(browser_config={"navigation_mode": mode}, engine_factory=factory)
    fetcher._ensure_engine()
    assert factory.call_args.kwargs["navigationMode"] == mode


def test_detail_failure_does_not_block_remaining_items():
    first_engine = MagicMock()
    first_engine.last_navigation_error = None
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
    assert "Detail page was not successfully read" in (
        results[1][0]["fetch_error"]
    )
    assert results[1][0]["fetch_error_category"] == "NAVIGATION_FAILED"
    assert results[1][0]["fetch_error_type"] == "RuntimeError"
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


@pytest.mark.parametrize("error_type,category", [
    ("ChallengeNotSolvedError", "CHALLENGE_UNRESOLVED"),
    ("SuccessCriteriaNotMetError", "CONTENT_CHECK_FAILED"),
    ("TimeoutException", "NAVIGATION_TIMEOUT"),
    ("TimeoutError", "NAVIGATION_TIMEOUT"),
    ("UnexpectedBrowserError", "NAVIGATION_FAILED"),
])
def test_navigation_failure_preserves_original_cause(error_type, category):
    engine = MagicMock()
    engine.navigate.return_value = False
    engine.last_navigation_error = {"type": error_type, "message": "original browser failure"}
    item = {"record_id": "7", "detail_url": "https://x/7", "list_data": {"name": "Example"}}
    fetcher = BrowserDetailFetcher(
        browser_config={}, storage_config={"reuse_saved_detail_pages": False},
        engine_factory=MagicMock(return_value=engine),
    )

    [(failed, response)] = list(fetcher.fetch([item]))

    assert response is None
    assert failed["fetch_error_category"] == category
    assert failed["fetch_error_type"] == error_type
    assert "original browser failure" in failed["fetch_error"]
    assert failed["record_id"] == item["record_id"]
    assert failed["list_data"] == item["list_data"]
    assert "fetch_error" not in item
    engine.getHtml.assert_not_called()


@pytest.mark.parametrize("failure,category,error_type", [
    ("selector", "CONTENT_NOT_READY", "RuntimeError"),
    ("empty", "EMPTY_HTML", "RuntimeError"),
    ("exception", "BROWSER_FETCH_FAILED", "ValueError"),
])
def test_browser_failure_categories_are_distinct(failure, category, error_type):
    engine = MagicMock()
    engine.navigate.return_value = True
    engine.waitForElement.return_value = failure != "selector"
    engine.getHtml.return_value = "" if failure == "empty" else "<html/>"
    if failure == "exception":
        engine.getHtml.side_effect = ValueError("cannot read DOM")
    fetcher = BrowserDetailFetcher(
        browser_config={"wait_selector": ".detail"},
        storage_config={"reuse_saved_detail_pages": False},
        engine_factory=MagicMock(return_value=engine),
    )

    [(failed, response)] = list(fetcher.fetch([{"detail_url": "https://x/7"}]))

    assert response is None
    assert failed["fetch_error_category"] == category
    assert failed["fetch_error_type"] == error_type
    engine.__exit__.assert_called_once_with(None, None, None)


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
