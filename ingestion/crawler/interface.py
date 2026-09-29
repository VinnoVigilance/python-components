from typing import Any

from ingestion.crawler.crawler import crawl_source
from ingestion.crawler.models import (
    CrawlerTask,
    CrawlResult,
)


def crawl(
    task: CrawlerTask,
    discovery_service: Any = None,
) -> CrawlResult:
    return crawl_source(
        task=task,
        discovery_service=discovery_service,
    )