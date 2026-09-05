from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from ingestion.crawler.configLoader import load_crawler_config
from ingestion.crawler.models import (
    CrawlerTask,
    CrawlResult,
)
from ingestion.crawler.storage import CrawlerStorage
from ingestion.crawler.spiders.genericSpider import GenericSpider
from ingestion.crawler.spiders.savedHtmlSpider import SavedHtmlSpider

def crawl_source(
    task: CrawlerTask,
) -> CrawlResult:
    crawler_config = load_crawler_config(
        task.source_config_path
    )

    fetch_strategy = str(
        crawler_config.get(
            "fetch_strategy",
            "direct",
        )
    ).strip().lower()

    storage_config = crawler_config.get(
        "storage",
        {},
    )

    detail_directory = storage_config.get(
        "detail_directory",
        "attachments/members",
    )

    if fetch_strategy == "saved_html":
        if not task.source_file_path:
            raise ValueError(
                "saved_html strategy requires "
                "task.source_file_path"
            )

        spider_class = SavedHtmlSpider
        storage = CrawlerStorage(
        source_name=task.source_name,
        list_name=task.list_name,
        base_dir=(
            task.download_dir
            or "data/downloads"
        ),
        detail_directory=detail_directory,
    )

    elif fetch_strategy == "direct":
        spider_class = GenericSpider

        storage = CrawlerStorage(
            source_name=task.source_name,
            list_name=task.list_name,
            base_dir=(
                task.download_dir
                or "data/downloads"
            ),
            detail_directory=detail_directory,
        )

    else:
        raise ValueError(
            f"Unsupported fetch_strategy: "
            f"{fetch_strategy}"
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
        spider_class,
        task=task,
        crawler_config=crawler_config,
        storage=storage,
        records=records,
    )

    process.start()

    if fetch_strategy == "saved_html":
        result_source_file_path = str(
            task.source_file_path
        )
    else:
        result_source_file_path = (
            str(storage.source_file_path)
            if storage.source_file_path.exists()
            else None
        )

    return CrawlResult(
        source_file_path=result_source_file_path,
        records=records,
        record_count=len(records),
    )