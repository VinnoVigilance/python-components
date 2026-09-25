import asyncio
import threading

from pathlib import Path
from unittest.mock import (
    MagicMock,
    call,
    patch,
)

import pytest

from scrapy.http import HtmlResponse

from ingestion.crawler.models import (
    CrawlerTask,
)
from ingestion.crawler.spiders.savedHtmlMediaSpider import (
    SavedHtmlMediaSpider,
)
from ingestion.crawler.storage import (
    CrawlerStorage,
)


pytestmark = pytest.mark.unit


def _build_spider(
    tmp_path,
):
    """
    Build a SavedHtmlMediaSpider without
    connecting to a real database or website.
    """

    source_config = {
        "source_name": "DOJ-PH",
        "dataset_name": "DOJ-PH-NEWS",

        "url": (
            "https://www.doj.gov.ph/"
            "news.html"
        ),

        "acquisition": {
            "type": "crawler",
            "spider": "media",
        },

        "discovery": {
            "strategy": "list_detail",
            "policy": "stop_after_known",
            "threshold": 10,

            "start_page": 1,

            "pagination": {
                "type": "none",
            },

            "article": {
                "link_selector": (
                    "a.news-link"
                ),
            },

        },

        "extraction": {
            "SourceURL": {
                "source": "response_url",
            },

            "SourceRecordId": {
                "source": "response_url",

                "extraction": {
                    "strategy": "regex",
                    "pattern": (
                        r"[?&]newsid=([^&]+)"
                    ),
                },
            },

            "Title": {
                "selector": "h1",
                "output": "text",
            },

            "BodyText": {
                "selector": "article",
                "output": "text",
            },
        },

        "detail_browser": {
            "headless": False,

            "wait_selector": "article",

            "timeout_seconds": 90,

            "success_criteria": [
                "Department of Justice",
            ],
        },

        "storage": {
            # Media must fetch the current detail
            # again to detect content changes.
            "reuse_saved_detail_pages": False,

            # Store Media details directly inside
            # the date directory.
            "detail_directory": "",
        },

        "identity": {
            "external_id": {
                "field": "SourceRecordId",
            },

            "record_key": {
                "fields": [
                    "source_name",
                    "dataset_name",
                    "SourceRecordId",
                ],
            },
        },
    }

    listing_file = (
        tmp_path
        / "doj_listing.html"
    )

    listing_file.write_text(
        "<html><body></body></html>",
        encoding="utf-8",
    )

    task = CrawlerTask(
        url=source_config["url"],
        source_name=(
            source_config["source_name"]
        ),
        list_name=(
            source_config["dataset_name"]
        ),
        source_config=source_config,
        source_file_path=str(
            listing_file
        ),
        download_dir=str(
            tmp_path
        ),
    )

    storage = CrawlerStorage(
        source_name=(
            source_config["source_name"]
        ),
        list_name=(
            source_config["dataset_name"]
        ),
        base_dir=str(
            tmp_path
        ),

        # Media files are stored directly in
        # the date directory.
        detail_directory="",
    )

    records = []

    discovery_service = MagicMock()

    spider = SavedHtmlMediaSpider(
        task=task,
        source_config=source_config,
        storage=storage,
        records=records,
        discovery_service=(
            discovery_service
        ),
    )

    return (
        spider,
        storage,
        records,
        discovery_service,
    )


def test_saved_listing_uses_media_discovery_and_stops(
    tmp_path,
):
    """
    The saved listing must reuse MediaSpider's:

    - SourceRecordId extraction
    - record_key generation
    - known/new checking
    - stop-condition decision
    """

    (
        spider,
        _,
        _,
        discovery_service,
    ) = _build_spider(
        tmp_path
    )

    listing_html = """
    <html>
        <body>
            <a
                class="news-link"
                href="news.html?newsid=101"
            >
                First article
            </a>

            <a
                class="news-link"
                href="news.html?newsid=102"
            >
                Second article
            </a>

            <a
                class="news-link"
                href="news.html?newsid=103"
            >
                Third article
            </a>
        </body>
    </html>
    """

    listing_response = HtmlResponse(
        url=(
            "https://www.doj.gov.ph/"
            "news.html"
        ),
        body=listing_html.encode(
            "utf-8"
        ),
        encoding="utf-8",
    )

    discovery_service.build_record_key.side_effect = [
        "DOJ-PH|DOJ-PH-NEWS|101",
        "DOJ-PH|DOJ-PH-NEWS|102",
    ]

    # Stop after the second discovered record.
    discovery_service.check_record_key.side_effect = [
        (
            False,
            False,
        ),
        (
            True,
            True,
        ),
    ]

    pending_details = (
        spider._discover_from_saved_listing(
            listing_response
        )
    )

    # The third article must not be processed
    # after the discovery service says stop.
    assert len(pending_details) == 1

    assert [
        item["source_record_id"]
        for item in pending_details
    ] == [
        "101",
    ]

    assert [
        item["record_key"]
        for item in pending_details
    ] == [
        "DOJ-PH|DOJ-PH-NEWS|101",
    ]

    assert pending_details[0][
        "is_known"
    ] is False

    assert (
        discovery_service
        .build_record_key
        .call_count
        == 2
    )

    assert (
        discovery_service
        .check_record_key
        .call_count
        == 2
    )


def test_start_yields_every_selected_detail(
    tmp_path,
):
    spider, _, _, _ = _build_spider(tmp_path)

    pending = [
        {"record_key": f"key-{index}"}
        for index in range(25)
    ]

    spider._discover_from_saved_listing = MagicMock(
        return_value=pending
    )
    main_thread_id = threading.get_ident()
    worker_thread_ids = []

    def fetch_details(items):
        worker_thread_ids.append(threading.get_ident())
        return iter(items)

    spider._fetch_and_parse_details = MagicMock(
        side_effect=fetch_details
    )

    async def collect():
        return [item async for item in spider.start()]

    results = asyncio.run(collect())

    assert results == pending
    assert len(worker_thread_ids) == 1
    assert worker_thread_ids[0] != main_thread_id
    spider._fetch_and_parse_details.assert_called_once_with(
        pending
    )


def test_known_doj_records_are_not_queued_for_download(
    tmp_path,
):
    (
        spider,
        _,
        _,
        discovery_service,
    ) = _build_spider(
        tmp_path
    )

    listing_response = HtmlResponse(
        url="https://www.doj.gov.ph/news.html",
        body=(
            b'<a class="news-link" href="?newsid=101">One</a>'
            b'<a class="news-link" href="?newsid=102">Two</a>'
            b'<a class="news-link" href="?newsid=103">Three</a>'
        ),
        encoding="utf-8",
    )

    discovery_service.build_record_key.side_effect = [
        "DOJ-PH|DOJ-PH-NEWS|101",
        "DOJ-PH|DOJ-PH-NEWS|102",
    ]
    discovery_service.check_record_key.side_effect = [
        (True, False),
        (True, True),
    ]

    pending_details = (
        spider._discover_from_saved_listing(
            listing_response
        )
    )

    assert pending_details == []
    assert (
        discovery_service.check_record_key.call_count
        == 2
    )


def test_media_details_are_saved_as_individual_files(
    tmp_path,
):
    """
    Every article must produce:

    - one separate HTML file
    - one detail_file_path
    - one Media-compatible acquisition record
    """

    (
        spider,
        storage,
        records,
        _,
    ) = _build_spider(
        tmp_path
    )

    first_url = (
        "https://www.doj.gov.ph/"
        "news.html?newsid=101"
    )

    second_url = (
        "https://www.doj.gov.ph/"
        "news.html?newsid=102"
    )

    pending_details = [
        {
            "detail_url": first_url,

            "record_key": (
                "DOJ-PH|"
                "DOJ-PH-NEWS|101"
            ),

            "is_known": False,

            "source_record_id": "101",
        },
        {
            "detail_url": second_url,

            "record_key": (
                "DOJ-PH|"
                "DOJ-PH-NEWS|102"
            ),

            "is_known": True,

            "source_record_id": "102",
        },
    ]

    first_html = """
    <html>
        <body>
            <h1>First DOJ Article</h1>

            <article>
                First article body.
            </article>
        </body>
    </html>
    """

    second_html = """
    <html>
        <body>
            <h1>Second DOJ Article</h1>

            <article>
                Second article body.
            </article>
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
            "savedHtmlMediaSpider."
            "StealthBrowserEngine"
        ),
        return_value=engine,
    ) as engine_class:

        results = list(
            spider._fetch_and_parse_details(
                pending_details
            )
        )

    # One browser session for both details.
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

    # One physical file for each news article.
    first_file = (
        storage.detail_path
        / (
            spider._build_file_name_id(
                pending_details[0]["record_key"]
            )
            + ".html"
        )
    )

    second_file = (
        storage.detail_path
        / (
            spider._build_file_name_id(
                pending_details[1]["record_key"]
            )
            + ".html"
        )
    )

    assert first_file.is_file()
    assert second_file.is_file()

    assert "First DOJ Article" in (
        first_file.read_text(
            encoding="utf-8"
        )
    )

    assert "Second DOJ Article" in (
        second_file.read_text(
            encoding="utf-8"
        )
    )

    assert len(results) == 2
    assert results == records

    # Exact contract expected by
    # MediaAcquisitionService.
    assert set(
        results[0].keys()
    ) == {
        "record_key",
        "is_known",
        "detail_file_path",
        "extracted",
    }

    assert results[0][
        "record_key"
    ] == (
        "DOJ-PH|"
        "DOJ-PH-NEWS|101"
    )

    assert results[0][
        "is_known"
    ] is False

    assert Path(
        results[0][
            "detail_file_path"
        ]
    ) == first_file

    assert results[0][
        "extracted"
    ] == {
        "SourceURL": first_url,
        "SourceRecordId": "101",
        "Title": "First DOJ Article",
        "BodyText": (
            "First article body."
        ),
    }

    assert results[1][
        "record_key"
    ] == (
        "DOJ-PH|"
        "DOJ-PH-NEWS|102"
    )

    assert results[1][
        "is_known"
    ] is True

    assert Path(
        results[1][
            "detail_file_path"
        ]
    ) == second_file

    assert results[1][
        "extracted"
    ] == {
        "SourceURL": second_url,
        "SourceRecordId": "102",
        "Title": "Second DOJ Article",
        "BodyText": (
            "Second article body."
        ),
    }
