from unittest.mock import MagicMock, call, patch

import pytest

from ingestion.crawler.crawler import crawl_source
from ingestion.crawler.models import CrawlerTask


pytestmark = pytest.mark.unit


def _settings_for(source_config, tmp_path):
    """Run crawl_source with Scrapy mocked and return the settings mock."""
    settings = MagicMock()
    with (
        patch("ingestion.crawler.crawler.get_project_settings", return_value=settings),
        patch("ingestion.crawler.crawler.CrawlerProcess", return_value=MagicMock()),
    ):
        crawl_source(CrawlerTask(
            url="https://example.test/list",
            source_name="TEST",
            list_name="TEST-LIST",
            source_config=source_config,
            download_dir=str(tmp_path),
        ))
    return settings


def test_watchlist_crawler_settings_are_applied(tmp_path):
    settings = _settings_for({
        "fetch_strategy": "direct",
        "crawler_settings": {
            "concurrent_requests_per_domain": 1,
            "download_delay": 2,
            "randomize_download_delay": True,
            "retry_times": 3,
            "retry_http_codes": [429, 503],
        },
    }, tmp_path)

    settings.set.assert_has_calls([
        call("CONCURRENT_REQUESTS_PER_DOMAIN", 1),
        call("DOWNLOAD_DELAY", 2.0),
        call("RANDOMIZE_DOWNLOAD_DELAY", True),
        call("RETRY_ENABLED", True),
        call("RETRY_TIMES", 3),
        call("RETRY_HTTP_CODES", [429, 503]),
    ])
    keys = [c.args[0] for c in settings.set.call_args_list]
    assert "DOWNLOADER_MIDDLEWARES" not in keys


def test_watchlist_without_crawler_settings_keeps_scrapy_defaults(tmp_path):
    settings = _settings_for({"fetch_strategy": "direct"}, tmp_path)

    keys = {c.args[0] for c in settings.set.call_args_list}
    assert keys == {"LOG_LEVEL", "USER_AGENT"}
