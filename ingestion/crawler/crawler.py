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
from ingestion.crawler.spiders.mediaSpider import MediaSpider


def crawl_source(
    task: CrawlerTask,
    discovery_service=None,
) -> CrawlResult:
    """
    Run crawler acquisition.

    Watchlist behaviour:
        - saved_html -> SavedHtmlSpider
        - direct     -> GenericSpider

    Adverse Media behaviour:
        - acquisition.spider=media -> MediaSpider

    Media-specific crawler settings are applied only
    to MediaSpider, so existing Watchlist behaviour
    remains unchanged.
    """

    # =====================================================
    # 1. LOAD CONFIG
    # =====================================================

    # Media passes the already-loaded source config
    # directly through CrawlerTask.
    source_config = getattr(
        task,
        "source_config",
        None,
    )

    if source_config:
        crawler_config = source_config

    else:
        # Existing Watchlist behaviour.
        crawler_config = load_crawler_config(
            task.source_config_path
        )

    # =====================================================
    # 2. DETECT MEDIA / WATCHLIST
    # =====================================================

    acquisition_config = crawler_config.get(
        "acquisition",
        {},
    )

    spider_type = str(
        acquisition_config.get(
            "spider",
            "",
        )
    ).strip().lower()

    is_media = (
        spider_type == "media"
    )

    # =====================================================
    # 3. STORAGE
    # =====================================================

    storage_config = crawler_config.get(
        "storage",
        {},
    )

    if is_media:
        # Media HTML files are stored directly
        # inside the date folder.
        detail_directory = storage_config.get(
            "detail_directory",
            "",
        )

    else:
        # IMPORTANT:
        # Preserve existing Watchlist behaviour.
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

    # =====================================================
    # 4. SELECT SPIDER
    # =====================================================

    if is_media:

        spider_class = MediaSpider

    else:

        # -------------------------------------------------
        # Existing Watchlist behaviour
        # -------------------------------------------------

        fetch_strategy = str(
            crawler_config.get(
                "fetch_strategy",
                task.fetch_strategy,
            )
        ).strip().lower()

        if fetch_strategy == "saved_html":

            if not task.source_file_path:
                raise ValueError(
                    "saved_html strategy requires "
                    "task.source_file_path"
                )

            spider_class = SavedHtmlSpider

        elif fetch_strategy == "direct":

            spider_class = GenericSpider

        else:

            raise ValueError(
                f"Unsupported fetch_strategy: "
                f"{fetch_strategy}"
            )

    # =====================================================
    # 5. RESULT CONTAINER
    # =====================================================

    records = []

    # =====================================================
    # 6. SCRAPY SETTINGS
    # =====================================================

    settings = get_project_settings()

    settings.set(
        "LOG_LEVEL",
        "INFO",
    )

    # =====================================================
    # 7. MEDIA-SPECIFIC HTTP SETTINGS
    # =====================================================
    #
    # IMPORTANT:
    # These settings are applied ONLY to Media.
    #
    # Watchlist continues using its existing
    # Scrapy settings exactly as before.
    # =====================================================

    if is_media:

        media_crawler_settings = (
            acquisition_config.get(
                "crawler_settings",
                {},
            )
        )

        # ---------------------------------------------
        # Concurrent requests
        # ---------------------------------------------

        if (
            "concurrent_requests_per_domain"
            in media_crawler_settings
        ):
            settings.set(
                "CONCURRENT_REQUESTS_PER_DOMAIN",
                int(
                    media_crawler_settings[
                        "concurrent_requests_per_domain"
                    ]
                ),
            )

        # ---------------------------------------------
        # Download delay
        # ---------------------------------------------

        if (
            "download_delay"
            in media_crawler_settings
        ):
            settings.set(
                "DOWNLOAD_DELAY",
                float(
                    media_crawler_settings[
                        "download_delay"
                    ]
                ),
            )

        # ---------------------------------------------
        # Randomized delay
        # ---------------------------------------------

        if (
            "randomize_download_delay"
            in media_crawler_settings
        ):
            settings.set(
                "RANDOMIZE_DOWNLOAD_DELAY",
                bool(
                    media_crawler_settings[
                        "randomize_download_delay"
                    ]
                ),
            )

        # ---------------------------------------------
        # Retry
        # ---------------------------------------------

        if media_crawler_settings:

            settings.set(
                "RETRY_ENABLED",
                True,
            )

        if (
            "retry_times"
            in media_crawler_settings
        ):
            settings.set(
                "RETRY_TIMES",
                int(
                    media_crawler_settings[
                        "retry_times"
                    ]
                ),
            )

        if (
            "retry_http_codes"
            in media_crawler_settings
        ):
            settings.set(
                "RETRY_HTTP_CODES",
                list(
                    media_crawler_settings[
                        "retry_http_codes"
                    ]
                ),
            )

        # ---------------------------------------------
        # Media retry backoff
        # ---------------------------------------------

        settings.set(
            "DOWNLOADER_MIDDLEWARES",
            {
                (
                    "scrapy.downloadermiddlewares."
                    "retry.RetryMiddleware"
                ): None,

                (
                    "ingestion.crawler.middlewares."
                    "mediaRetryMiddleware."
                    "MediaRetryMiddleware"
                ): 550,
            },
        )

    # =====================================================
    # 8. CREATE CRAWLER PROCESS
    # =====================================================

    process = CrawlerProcess(
        settings=settings,
    )

    # =====================================================
    # 9. RUN SPIDER
    # =====================================================

    if is_media:

        # IMPORTANT:
        # MediaSpider expects:
        #
        #     source_config
        #
        # not:
        #
        #     crawler_config
        #
        process.crawl(
            spider_class,
            task=task,
            source_config=crawler_config,
            storage=storage,
            records=records,
            discovery_service=discovery_service,
        )

    else:

        # -------------------------------------------------
        # Existing Watchlist call.
        #
        # DO NOT change parameter names here.
        # GenericSpider / SavedHtmlSpider already use
        # crawler_config.
        # -------------------------------------------------

        process.crawl(
            spider_class,
            task=task,
            crawler_config=crawler_config,
            storage=storage,
            records=records,
        )

    # =====================================================
    # 10. START
    # =====================================================

    process.start()

    # =====================================================
    # 11. RESULT SOURCE FILE
    # =====================================================

    if is_media:

        # Media works with one HTML file per article.
        # Therefore there is no single source file.
        result_source_file_path = None

    else:

        fetch_strategy = str(
            crawler_config.get(
                "fetch_strategy",
                task.fetch_strategy,
            )
        ).strip().lower()

        if fetch_strategy == "saved_html":

            result_source_file_path = str(
                task.source_file_path
            )

        else:

            result_source_file_path = (
                str(
                    storage.source_file_path
                )
                if storage.source_file_path.exists()
                else None
            )

    # =====================================================
    # 12. RESULT
    # =====================================================

    return CrawlResult(
        source_file_path=result_source_file_path,
        records=records,
        record_count=len(records),
    )