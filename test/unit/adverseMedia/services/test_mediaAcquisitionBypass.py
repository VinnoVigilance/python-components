import os

from pathlib import Path
from unittest.mock import (
    MagicMock,
)

import pytest


pytest.importorskip(
    "boto3",
    reason=(
        "Full pipeline stack "
        "(boto3) not installed"
    ),
)

os.environ.setdefault(
    "DB_PASSWORD",
    "test_dummy",
)

os.environ.setdefault(
    "STORAGE_SECRET_KEY_INGESTION",
    "test_dummy",
)


from services.adverseMediaPipeline import (  # noqa: E402
    mediaAcquisitionService as module,
)


pytestmark = pytest.mark.unit


def _mock_database(
    monkeypatch,
):
    connection = MagicMock()
    cursor = MagicMock()

    connection.cursor.return_value.__enter__.return_value = (
        cursor
    )

    connection_pool = MagicMock()

    connection_pool.getconn.return_value = (
        connection
    )

    monkeypatch.setattr(
        module,
        "connection_pool",
        connection_pool,
    )

    discovery_service = MagicMock()

    discovery_service_class = MagicMock(
        return_value=discovery_service
    )

    monkeypatch.setattr(
        module,
        "MediaDiscoveryService",
        discovery_service_class,
    )

    return (
        connection,
        connection_pool,
        discovery_service,
        discovery_service_class,
    )


def test_saved_html_media_collects_listing_before_crawl(
    tmp_path,
    monkeypatch,
):
    """
    Protected Media must:

    - run BypassCollector first
    - pass the saved listing to CrawlerTask
    - use fetch_strategy=saved_html
    """

    listing_file = (
        tmp_path
        / "doj_listing.html"
    )

    listing_file.write_text(
        "<html><body>DOJ listing</body></html>",
        encoding="utf-8",
    )

    collector = MagicMock()

    collector.collect.return_value = (
        listing_file
    )

    collector_class = MagicMock(
        return_value=collector
    )

    monkeypatch.setattr(
        module,
        "BypassCollector",
        collector_class,
    )

    (
        connection,
        connection_pool,
        discovery_service,
        discovery_service_class,
    ) = _mock_database(
        monkeypatch
    )

    crawl_result = MagicMock()

    crawl_mock = MagicMock(
        return_value=crawl_result
    )

    monkeypatch.setattr(
        module,
        "crawl",
        crawl_mock,
    )

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
            "fetch_strategy": (
                "saved_html"
            ),
            "download_method": "BYPASS",
        },

        "bypass_config": {
            "challenge": "cloudflare",

            "actions": [
                {
                    "action": "save_html",
                }
            ],
        },
    }

    result = (
        module.MediaAcquisitionService
        ._crawl_source(
            source_config=source_config,
            source_id=10,
            dataset_id=20,
            known_threshold=10,
            source_name="DOJ-PH",
            dataset_name="DOJ-PH-NEWS",
            source_url=source_config[
                "url"
            ],
        )
    )

    assert result is crawl_result

    collector_class.assert_called_once()

    collector.collect.assert_called_once()

    bypass_config_passed = (
        collector.collect.call_args.args[0]
    )

    assert bypass_config_passed[
        "list_name"
    ] == "DOJ-PH-NEWS"

    # The original Media configuration must
    # not be mutated.
    assert "list_name" not in source_config

    crawl_mock.assert_called_once()

    crawler_task = (
        crawl_mock.call_args.kwargs[
            "task"
        ]
    )

    assert (
        crawler_task.fetch_strategy
        == "saved_html"
    )

    assert Path(
        crawler_task.source_file_path
    ) == listing_file.resolve()

    assert (
        crawler_task.source_config
        is source_config
    )

    assert (
        crawl_mock.call_args.kwargs[
            "discovery_service"
        ]
        is discovery_service
    )

    discovery_service_class.assert_called_once()

    connection_pool.putconn.assert_called_once_with(
        connection
    )


def test_direct_media_does_not_run_bypass_collector(
    monkeypatch,
):
    """
    Existing direct Media sources such as NBI
    must not run BypassCollector.
    """

    collector_class = MagicMock()

    monkeypatch.setattr(
        module,
        "BypassCollector",
        collector_class,
    )

    (
        connection,
        connection_pool,
        discovery_service,
        _,
    ) = _mock_database(
        monkeypatch
    )

    crawl_result = MagicMock()

    crawl_mock = MagicMock(
        return_value=crawl_result
    )

    monkeypatch.setattr(
        module,
        "crawl",
        crawl_mock,
    )

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
            "type": "crawler",
            "spider": "media",
            "fetch_strategy": "direct",
            "download_method": "CRAWLER",
        },
    }

    result = (
        module.MediaAcquisitionService
        ._crawl_source(
            source_config=source_config,
            source_id=1,
            dataset_id=2,
            known_threshold=10,
            source_name="NBI",
            dataset_name=(
                "NBI_PRESS_RELEASES"
            ),
            source_url=source_config[
                "url"
            ],
        )
    )

    assert result is crawl_result

    collector_class.assert_not_called()

    crawler_task = (
        crawl_mock.call_args.kwargs[
            "task"
        ]
    )

    assert (
        crawler_task.fetch_strategy
        == "direct"
    )

    assert (
        crawler_task.source_file_path
        is None
    )

    assert (
        crawl_mock.call_args.kwargs[
            "discovery_service"
        ]
        is discovery_service
    )

    connection_pool.putconn.assert_called_once_with(
        connection
    )


def test_saved_html_media_requires_bypass_config():
    """
    Invalid protected Media configuration must
    fail before browser or database work.
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
            "fetch_strategy": (
                "saved_html"
            ),
        },
    }

    with pytest.raises(
        ValueError,
        match="bypass_config",
    ):
        (
            module.MediaAcquisitionService
            ._validate_source_config(
                source_config
            )
        )