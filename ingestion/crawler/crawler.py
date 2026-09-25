from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import (
    get_project_settings,
)

from ingestion.crawler.configLoader import (
    load_crawler_config,
)
from ingestion.crawler.models import (
    CrawlerTask,
    CrawlResult,
)
from ingestion.crawler.storage import (
    CrawlerStorage,
)
from ingestion.crawler.spiders.genericSpider import (
    GenericSpider,
)
from ingestion.crawler.spiders.mediaSpider import (
    MediaSpider,
)
from ingestion.crawler.spiders.savedHtmlMediaSpider import (
    SavedHtmlMediaSpider,
)
from ingestion.crawler.spiders.savedHtmlSpider import (
    SavedHtmlSpider,
)


def _finalize_media_completion(
    discovery_summary: dict,
    record_count: int,
) -> tuple[int, int, str | None, bool | None]:
    """Validate that every selected detail produced a result record."""

    selected_detail_count = int(
        discovery_summary.get(
            "selected_detail_count",
            0,
        )
    )

    missing_detail_count = max(
        selected_detail_count - record_count,
        0,
    )

    stop_reason = discovery_summary.get(
        "stop_reason"
    )

    completed_safely = discovery_summary.get(
        "completed_safely"
    )

    if missing_detail_count:
        stop_reason = "DETAIL_RESULTS_INCOMPLETE"
        completed_safely = False

    return (
        selected_detail_count,
        missing_detail_count,
        stop_reason,
        completed_safely,
    )


def crawl_source(
    task: CrawlerTask,
    discovery_service=None,
) -> CrawlResult:
    """
    Run crawler acquisition.

    Watchlist behaviour:

        direct
            -> GenericSpider

        saved_html
            -> SavedHtmlSpider

    Adverse Media behaviour:

        direct
            -> MediaSpider

        saved_html
            -> SavedHtmlMediaSpider

    Media-specific crawler settings are applied
    only to Media spiders. Existing Watchlist
    behaviour remains unchanged.
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
        # Media stores each detail HTML directly
        # inside the date directory.
        detail_directory = storage_config.get(
            "detail_directory",
            "",
        )

    else:
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

        # ---------------------------------------------
        # Adverse Media strategy
        # ---------------------------------------------

        media_fetch_strategy = str(
            acquisition_config.get(
                "fetch_strategy",

                # Backward compatibility:
                # allow the strategy at the top level
                # or through CrawlerTask.
                crawler_config.get(
                    "fetch_strategy",
                    task.fetch_strategy,
                ),
            )
        ).strip().lower()

        if media_fetch_strategy == "direct":

            # Existing direct Media behaviour,
            # including NBI.
            spider_class = MediaSpider

        elif media_fetch_strategy == "saved_html":

            if not task.source_file_path:
                raise ValueError(
                    "Media saved_html strategy "
                    "requires task.source_file_path."
                )

            spider_class = (
                SavedHtmlMediaSpider
            )

        else:

            raise ValueError(
                "Unsupported Media "
                "fetch_strategy: "
                f"{media_fetch_strategy}"
            )

    else:

        # ---------------------------------------------
        # Existing Watchlist strategy
        # ---------------------------------------------

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
                "Unsupported fetch_strategy: "
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

    settings.set(
        "USER_AGENT",
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/126.0.0.0 "
        "Safari/537.36",
    )

    # =====================================================
    # 7. MEDIA-SPECIFIC HTTP SETTINGS
    # =====================================================
    #
    # These settings are applied only to Media.
    # Existing Watchlist Scrapy settings remain
    # unchanged.
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

        # Both MediaSpider and SavedHtmlMediaSpider
        # expect source_config and discovery_service.
        process.crawl(
            spider_class,
            task=task,
            source_config=crawler_config,
            storage=storage,
            records=records,
            discovery_service=discovery_service,
        )

    else:

        # Preserve existing Watchlist parameter names.
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
        # Therefore there is no single source file
        # returned as a Media acquisition artifact.
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

    discovery_summary = {}

    if (
        is_media
        and discovery_service is not None
        and hasattr(
            discovery_service,
            "get_summary",
        )
    ):
        discovery_summary = (
            discovery_service.get_summary()
        )

    (
        selected_detail_count,
        missing_detail_count,
        stop_reason,
        completed_safely,
    ) = _finalize_media_completion(
        discovery_summary=discovery_summary,
        record_count=len(records),
    )

    return CrawlResult(
        source_file_path=(
            result_source_file_path
        ),
        records=records,
        record_count=len(records),
        discovered_count=(
            discovery_summary.get(
                "discovered_count"
            )
        ),
        known_count=discovery_summary.get(
            "known_count",
            0,
        ),
        new_count=discovery_summary.get(
            "new_count",
            0,
        ),
        selected_detail_count=(
            selected_detail_count
        ),
        missing_detail_count=(
            missing_detail_count
        ),
        identity_failure_count=(
            discovery_summary.get(
                "identity_failure_count",
                0,
            )
        ),
        discovery_failure_count=(
            discovery_summary.get(
                "discovery_failure_count",
                0,
            )
        ),
        reached_source_end=(
            discovery_summary.get(
                "reached_source_end"
            )
        ),
        stop_reason=stop_reason,
        completed_safely=completed_safely,
    )
