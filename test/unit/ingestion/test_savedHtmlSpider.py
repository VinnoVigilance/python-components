import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from unittest.mock import (
    MagicMock,
    call,
    patch,
)

import pytest

from ingestion.crawler.models import (
    CrawlerTask,
)
from ingestion.crawler.spiders.savedHtmlSpider import (
    SavedHtmlSpider,
)
from ingestion.crawler.storage import (
    CrawlerStorage,
)


pytestmark = pytest.mark.unit


def _build_spider(
    tmp_path,
    *,
    reuse_saved_pages: bool,
):
    """
    Build SavedHtmlSpider with its current
    Watchlist configuration contract.
    """

    crawler_config = {
        "record_mode": "list_detail",

        "detail_fetch_strategy": "browser",

        "detail_browser": {
            "headless": False,
            "wait_selector": "h1",
            "timeout_seconds": 90,
            "success_criteria": [
                "Profile",
            ],
        },

        "storage": {
            "save_detail_pages": True,
            "reuse_saved_detail_pages": (
                reuse_saved_pages
            ),
            "minimum_detail_size_bytes": 10,
        },

        "list_fields": {
            "full_name": {
                "selector": "a",
                "value": "text",
            },
        },

        "detail_fields": {
            "profile_name": {
                "selector": "h1",
                "value": "text",
            },
        },

        "attachments": [],
    }

    task = CrawlerTask(
        url="https://example.test/list",
        source_name="TEST-SOURCE",
        list_name="TEST-LIST",
    )

    storage = CrawlerStorage(
        source_name="TEST-SOURCE",
        list_name="TEST-LIST",
        base_dir=str(tmp_path),
        detail_directory=(
            "attachments/members"
        ),
    )

    records = []

    spider = SavedHtmlSpider(
        task=task,
        crawler_config=crawler_config,
        storage=storage,
        records=records,
    )

    return (
        spider,
        storage,
        records,
    )


def test_browser_details_are_fetched_sequentially_and_saved(
    tmp_path,
):
    """
    Characterization test:

    - only one browser engine is created
    - detail pages are visited sequentially
    - one HTML file is saved per Watchlist record
    - the existing Watchlist record shape is preserved
    """

    (
        spider,
        storage,
        records,
    ) = _build_spider(
        tmp_path,
        reuse_saved_pages=False,
    )

    first_url = (
        "https://example.test/profile/101"
    )

    second_url = (
        "https://example.test/profile/102"
    )

    pending_details = [
        {
            "list_data": {
                "full_name": "Jane Doe",
            },
            "record_id": "101",
            "detail_url": first_url,
        },
        {
            "list_data": {
                "full_name": "John Doe",
            },
            "record_id": "102",
            "detail_url": second_url,
        },
    ]

    first_html = """
    <html>
        <body>
            <h1>Jane Doe Profile</h1>
        </body>
    </html>
    """

    second_html = """
    <html>
        <body>
            <h1>John Doe Profile</h1>
        </body>
    </html>
    """

    engine = MagicMock()

    engine.navigate.return_value = True
    engine.waitForElement.return_value = True

    engine.getHtml.side_effect = [
        first_html,
        second_html,
    ]

    with patch(
        (
            "ingestion.crawler.spiders."
            "savedHtmlSpider."
            "StealthBrowserEngine"
        ),
        return_value=engine,
    ) as engine_class:

        results = list(
            spider._fetch_browser_details(
                pending_details
            )
        )

    # One browser is shared by all detail pages.
    engine_class.assert_called_once()

    engine.__enter__.assert_called_once()

    engine.__exit__.assert_called_once_with(
        None,
        None,
        None,
    )

    engine.navigate.assert_has_calls(
        [
            call(first_url),
            call(second_url),
        ]
    )

    assert engine.navigate.call_count == 2

    # One physical HTML file per record.
    first_file = (
        storage.detail_path
        / "101.html"
    )

    second_file = (
        storage.detail_path
        / "102.html"
    )

    assert first_file.is_file()
    assert second_file.is_file()

    assert "Jane Doe Profile" in (
        first_file.read_text(
            encoding="utf-8"
        )
    )

    assert "John Doe Profile" in (
        second_file.read_text(
            encoding="utf-8"
        )
    )

    # Existing Watchlist output contract.
    assert len(results) == 2
    assert results == records

    assert results[0][
        "source_record_id"
    ] == "101"

    assert results[0]["list"] == {
        "full_name": "Jane Doe",
    }

    assert results[0]["detail"] == {
        "profile_name": (
            "Jane Doe Profile"
        ),
    }

    assert results[0]["attachments"] == [
        {
            "type": "DETAIL_PAGE",
            "url": first_url,
        }
    ]

    assert results[1][
        "source_record_id"
    ] == "102"

    assert results[1]["detail"] == {
        "profile_name": (
            "John Doe Profile"
        ),
    }


def test_existing_saved_detail_is_reused_without_browser(
    tmp_path,
):
    """
    Characterization test:

    When reuse_saved_detail_pages is enabled,
    an existing valid HTML file is parsed without
    starting the browser.
    """

    (
        spider,
        storage,
        records,
    ) = _build_spider(
        tmp_path,
        reuse_saved_pages=True,
    )

    detail_url = (
        "https://example.test/profile/200"
    )

    cached_file = (
        storage.detail_path
        / "200.html"
    )

    cached_file.write_text(
        """
        <html>
            <body>
                <h1>Cached Profile</h1>
            </body>
        </html>
        """,
        encoding="utf-8",
    )

    pending_details = [
        {
            "list_data": {
                "full_name": (
                    "Cached Person"
                ),
            },
            "record_id": "200",
            "detail_url": detail_url,
        }
    ]

    with patch(
        (
            "ingestion.crawler.spiders."
            "savedHtmlSpider."
            "StealthBrowserEngine"
        )
    ) as engine_class:

        results = list(
            spider._fetch_browser_details(
                pending_details
            )
        )

    # Browser must not be created for a reusable file.
    engine_class.assert_not_called()

    assert len(results) == 1
    assert results == records

    assert results[0][
        "source_record_id"
    ] == "200"

    assert results[0]["detail"] == {
        "profile_name": "Cached Profile",
    }

    assert results[0]["attachments"] == [
        {
            "type": "DETAIL_PAGE",
            "url": detail_url,
        }
    ]