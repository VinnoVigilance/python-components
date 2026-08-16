from ingestion.crawler.crawler import crawl_source
from ingestion.crawler.models import (
    CrawlerTask,
    CrawlResult,
)


def crawl(
    task: CrawlerTask,
) -> CrawlResult:
    return crawl_source(task)