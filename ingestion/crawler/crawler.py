from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from ingestion.crawler.configLoader import load_crawler_config
from ingestion.crawler.models import (
    CrawlerTask,
    CrawlResult,
)
from ingestion.crawler.storage import CrawlerStorage
from ingestion.crawler.spiders.genericSpider import GenericSpider


def crawl_source(
    task: CrawlerTask,
) -> CrawlResult:
    crawler_config = load_crawler_config(
        task.source_config_path
    )

    storage_config = crawler_config.get(
        "storage",
        {},
    )

    detail_directory = storage_config.get(
        "detail_directory",
        "attachments/members",
    )

    storage = CrawlerStorage(
        source_name=task.source_name,
        list_name=task.list_name,
        base_dir=(
            task.download_dir
            or "data/downloads"
        ),
        detail_directory=detail_directory,
    )

    records = []

    settings = get_project_settings()

    settings.set(
        "LOG_LEVEL",
        "INFO",
    )

    process = CrawlerProcess(
        settings=settings,
    )

    process.crawl(
        GenericSpider,
        task=task,
        crawler_config=crawler_config,
        storage=storage,
        records=records,
    )

    process.start()

    return CrawlResult(
        source_file_path=(
            str(storage.source_file_path)
            if storage.source_file_path.exists()
            else None
        ),
        records=records,
        record_count=len(records),
    )