import subprocess
import sys
from textwrap import dedent

import pytest


@pytest.mark.unit
@pytest.mark.parametrize("scenario", ["paced_retry", "abs_cbn_limit"])
def test_chrome_downloads_respect_scrapy_queue(tmp_path, scenario):
    # Scrapy owns a process-wide reactor, so verify the real queue in a fresh
    # process. Mock the HTTP exchange only; no network requests are made.
    script = dedent("""
        import sys
        import time
        from threading import Event, Lock
        from types import SimpleNamespace
        from unittest.mock import patch

        from curl_cffi.requests import Headers
        from curl_cffi.requests.exceptions import RequestException
        from scrapy.settings import Settings

        from ingestion.crawler.crawler import crawl_source
        from ingestion.crawler.models import CrawlerTask
        from scripts.adverseMedia._media_harness import get_media_config

        scenario = sys.argv[2]
        high_concurrency = scenario == "abs_cbn_limit"
        expected_peak = 100 if high_concurrency else 1
        starts = []
        inflight = peak = 0
        failed_once = set()
        lock = Lock()
        filled = Event()

        def exchange(method, url, **options):
            global inflight, peak
            assert options["impersonate"] == "chrome"
            assert options["allow_redirects"] is False
            with lock:
                inflight += 1
                peak = max(peak, inflight)
                starts.append((url, time.monotonic()))
                if inflight == expected_peak:
                    filled.set()
            try:
                if high_concurrency and not filled.wait(timeout=8):
                    raise AssertionError("The transport could not fill 100 Scrapy slots")
                if url.endswith("/retry") and url not in failed_once:
                    failed_once.add(url)
                    time.sleep(0.7)
                    raise RequestException("temporary connection error")
                return SimpleNamespace(
                    status_code=200,
                    headers=Headers({"content-type": "text/html; charset=utf-8"}),
                    content=b"<h1>Example</h1>",
                )
            finally:
                with lock:
                    inflight -= 1

        article_names = range(104) if high_concurrency else ("retry", "next", "last")
        urls = [f"https://example.test/{item}" for item in article_names]
        if high_concurrency:
            _, source = get_media_config("ABS_CBN_NEWS")
            settings = source["acquisition"]["crawler_settings"]
            assert settings["download_delay"] == 0
            assert settings["concurrent_requests_per_domain"] == expected_peak
            assert settings["concurrent_requests"] == expected_peak
        else:
            settings = {
                "transport": "chrome",
                "concurrent_requests_per_domain": 1,
                "download_delay": 0.5,
                "randomize_download_delay": False,
                "retry_times": 1,
            }
        task = CrawlerTask(
            url=urls[0], source_name="TEST", list_name="TRANSPORT",
            download_dir=sys.argv[1],
            detail_items=[{"detail_url": url, "record_key": url} for url in urls],
            source_config={
                "acquisition": {
                    "spider": "media",
                    "crawler_settings": settings,
                },
                "extraction": {"Title": {"selector": "h1"}},
            },
        )
        with (
            patch("ingestion.crawler.chromeDownloadHandler.requests.request", exchange),
            patch("ingestion.crawler.crawler.get_project_settings",
                  return_value=Settings({"TELNETCONSOLE_ENABLED": False})),
        ):
            result = crawl_source(task)

        assert peak == expected_peak, (peak, starts)
        if high_concurrency:
            assert len(starts) == len(urls)
        else:
            assert len(starts) == 4, starts
            assert [url for url, _ in starts].count(urls[0]) == 2, starts
            assert all(b - a >= 0.48 for (_, a), (_, b) in zip(starts, starts[1:])), starts
        assert len(result.records) == len(urls)
        assert all(rec.get("extracted", {}).get("Title") == "Example" for rec in result.records)
    """)
    output_dir = str(tmp_path)
    if sys.platform == "win32" and not output_dir.startswith("\\\\?\\"):
        output_dir = "\\\\?\\" + output_dir
    process = subprocess.run(
        [sys.executable, "-c", script, output_dir, scenario],
        capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
