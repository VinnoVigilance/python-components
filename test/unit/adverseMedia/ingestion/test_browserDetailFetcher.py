from unittest.mock import MagicMock

import pytest

from ingestion.crawler.browserDetailFetcher import (
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
