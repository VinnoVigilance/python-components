from unittest.mock import (
    MagicMock,
    patch,
)

import pytest

from ingestion.crawler.crawler import (
    crawl_source,
)
from ingestion.crawler.models import (
    CrawlerTask,
)
from ingestion.crawler.spiders.mediaSpider import (
    MediaSpider,
)
from ingestion.crawler.spiders.savedHtmlMediaSpider import (
    SavedHtmlMediaSpider,
)
from ingestion.crawler.spiders.savedHtmlSpider import (
    SavedHtmlSpider,
)


pytestmark = pytest.mark.unit


def _run_crawler_without_starting_scrapy(
    task,
    discovery_service=None,
):
    """
    Run crawler orchestration while mocking
    CrawlerProcess so no real spider or network
    request is started.
    """

    settings = MagicMock()
    process = MagicMock()

    with (
        patch(
            (
                "ingestion.crawler.crawler."
                "get_project_settings"
            ),
            return_value=settings,
        ),
        patch(
            (
                "ingestion.crawler.crawler."
                "CrawlerProcess"
            ),
            return_value=process,
        ),
    ):
        result = crawl_source(
            task=task,
            discovery_service=(
                discovery_service
            ),
        )

    selected_spider = (
        process.crawl.call_args.args[0]
    )

    return (
        selected_spider,
        process,
        result,
    )


def test_direct_media_still_uses_media_spider(
    tmp_path,
):
    """
    Existing direct Media sources such as NBI
    must continue using MediaSpider.
    """

    source_config = {
        "source_name": "NBI",
        "dataset_name": (
            "NBI_PRESS_RELEASES"
        ),
        "url": (
            "https://nbi.gov.ph/"
            "press_releases/"
        ),

        "acquisition": {
            "spider": "media",
            "fetch_strategy": "direct",
        },
    }

    task = CrawlerTask(
        url=source_config["url"],
        source_name=(
            source_config["source_name"]
        ),
        list_name=(
            source_config["dataset_name"]
        ),
        source_config=source_config,
        download_dir=str(tmp_path),
    )

    discovery_service = MagicMock()

    (
        selected_spider,
        process,
        result,
    ) = _run_crawler_without_starting_scrapy(
        task=task,
        discovery_service=(
            discovery_service
        ),
    )

    assert selected_spider is MediaSpider

    crawl_kwargs = (
        process.crawl.call_args.kwargs
    )

    assert crawl_kwargs[
        "discovery_service"
    ] is discovery_service

    assert crawl_kwargs[
        "source_config"
    ] is source_config

    assert result.source_file_path is None


def test_saved_html_media_uses_saved_html_media_spider(
    tmp_path,
):
    """
    Protected Media sources must use
    SavedHtmlMediaSpider.
    """

    listing_file = (
        tmp_path
        / "doj_listing.html"
    )

    listing_file.write_text(
        "<html><body>DOJ</body></html>",
        encoding="utf-8",
    )

    source_config = {
        "source_name": "DOJ-PH",
        "dataset_name": "DOJ-PH-NEWS",
        "url": (
            "https://www.doj.gov.ph/"
            "news.html"
        ),

        "acquisition": {
            "spider": "media",
            "fetch_strategy": (
                "saved_html"
            ),
        },
    }

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
        download_dir=str(tmp_path),
    )

    discovery_service = MagicMock()

    (
        selected_spider,
        process,
        result,
    ) = _run_crawler_without_starting_scrapy(
        task=task,
        discovery_service=(
            discovery_service
        ),
    )

    assert (
        selected_spider
        is SavedHtmlMediaSpider
    )

    crawl_kwargs = (
        process.crawl.call_args.kwargs
    )

    assert crawl_kwargs[
        "discovery_service"
    ] is discovery_service

    assert crawl_kwargs[
        "task"
    ] is task

    # The listing itself is not returned as a
    # Media detail file.
    assert result.source_file_path is None


def test_saved_html_watchlist_still_uses_existing_spider(
    tmp_path,
):
    """
    Existing Watchlist sources must continue
    using the original SavedHtmlSpider.
    """

    listing_file = (
        tmp_path
        / "watchlist_listing.html"
    )

    listing_file.write_text(
        "<html><body>Watchlist</body></html>",
        encoding="utf-8",
    )

    crawler_config = {
        "fetch_strategy": "saved_html",

        # No acquisition.spider=media means this
        # remains a Watchlist crawler.
        "record_mode": "list_detail",
    }

    task = CrawlerTask(
        url=(
            "https://example.test/"
            "watchlist"
        ),
        source_name="TEST-WATCHLIST",
        list_name="TEST-WATCHLIST",
        source_config=crawler_config,
        source_file_path=str(
            listing_file
        ),
        download_dir=str(tmp_path),
    )

    (
        selected_spider,
        process,
        result,
    ) = _run_crawler_without_starting_scrapy(
        task=task,
    )

    assert selected_spider is SavedHtmlSpider

    crawl_kwargs = (
        process.crawl.call_args.kwargs
    )

    assert crawl_kwargs[
        "crawler_config"
    ] is crawler_config

    assert (
        "discovery_service"
        not in crawl_kwargs
    )

    assert result.source_file_path == str(
        listing_file
    )