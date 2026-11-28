from unittest.mock import MagicMock, call, patch

import pytest

from ingestion.crawler.crawler import crawl_source
from ingestion.crawler.models import CrawlerTask


pytestmark = pytest.mark.unit


def _settings_for(source_config, tmp_path, timeout=30):
    """Run crawl_source with Scrapy mocked and return the settings mock."""
    settings = MagicMock()
    task = CrawlerTask(
        url="https://example.test/list",
        source_name="TEST",
        list_name="TEST-LIST",
        source_config=source_config,
        download_dir=str(tmp_path),
        timeout=timeout,
    )
    with (
        patch("ingestion.crawler.crawler.get_project_settings", return_value=settings),
        patch("ingestion.crawler.crawler.CrawlerProcess", return_value=MagicMock()),
    ):
        crawl_source(task)
    return settings, task


def test_watchlist_crawler_settings_are_applied(tmp_path):
    settings, _ = _settings_for({
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
    settings, task = _settings_for({"fetch_strategy": "direct"}, tmp_path, timeout=45)

    keys = {c.args[0] for c in settings.set.call_args_list}
    assert keys == {"LOG_LEVEL", "USER_AGENT"}
    assert task.timeout == 45


def _timeout_config(kind, settings):
    if kind == "media":
        return {"acquisition": {"spider": "media", "crawler_settings": settings}}
    return {"fetch_strategy": "direct", "crawler_settings": settings}


@pytest.mark.parametrize("kind", ["media", "watchlist"])
@pytest.mark.parametrize("timeout", [900, 12.5])
def test_configured_download_timeout_applies_to_task_and_scrapy(tmp_path, kind, timeout):
    settings, task = _settings_for(
        _timeout_config(kind, {"download_timeout": timeout}), tmp_path
    )

    assert task.timeout == timeout
    settings.set.assert_any_call("DOWNLOAD_TIMEOUT", timeout)


@pytest.mark.parametrize("kind", ["media", "watchlist"])
@pytest.mark.parametrize("timeout", [True, False, None, "900", 0, -1, float("inf"), float("nan")])
def test_invalid_download_timeout_is_rejected(tmp_path, kind, timeout):
    with pytest.raises(ValueError, match="download_timeout must be a finite positive number"):
        _settings_for(_timeout_config(kind, {"download_timeout": timeout}), tmp_path)


@pytest.mark.parametrize("kind", ["media", "watchlist"])
def test_other_crawler_settings_preserve_existing_timeout(tmp_path, kind):
    settings, task = _settings_for(
        _timeout_config(kind, {"retry_times": 2}), tmp_path, timeout=45
    )

    assert task.timeout == 45
    assert "DOWNLOAD_TIMEOUT" not in {c.args[0] for c in settings.set.call_args_list}
