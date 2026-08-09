"""Run crawler spiders without mixing them into the business pipeline."""

from typing import Any

from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from ingestion.crawler import settings as crawler_settings
from ingestion.crawler.spiders.genericArticleSpider import GenericArticleSpider


def run_article_crawler(source_config: dict[str, Any]) -> dict[str, Any]:
    """Run the adverse-media article spider and return its downloaded files."""
    settings = Settings()
    settings.setmodule(crawler_settings, priority="project")

    if source_config["acquisition"].get("engine") == "playwright":
        _enable_playwright(settings)

    process = CrawlerProcess(settings=settings, install_root_handler=False)
    crawler = process.create_crawler(GenericArticleSpider)
    result_files: list[dict[str, object]] = []
    process.crawl(
        crawler,
        source_config=source_config,
        result_files=result_files,
    )
    process.start()

    stats = crawler.stats.get_stats()
    files = list(result_files)

    return {
        "status": str(stats.get("finish_reason", "unknown")),
        "files": files,
        "downloaded_article_count": len(files),
        "error_count": int(stats.get("log_count/ERROR", 0)),
    }


def _enable_playwright(settings: Settings) -> None:
    try:
        import scrapy_playwright  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "The Playwright engine requires scrapy-playwright and playwright"
        ) from error

    settings.set(
        "DOWNLOAD_HANDLERS",
        {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        priority="project",
    )
    settings.set(
        "TWISTED_REACTOR",
        "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        priority="project",
    )
