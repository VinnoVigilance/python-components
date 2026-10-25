import logging
import os

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


logger = logging.getLogger(__name__)


def _resolve_user_agent(crawler_settings: dict) -> str | None:
    """Resolve an optional source-specific HTTP identity without changing defaults."""
    direct = "user_agent" in crawler_settings
    from_env = "user_agent_env" in crawler_settings
    if direct and from_env:
        raise ValueError("Configure only one of user_agent or user_agent_env")
    if not direct and not from_env:
        return None

    if from_env:
        name = crawler_settings["user_agent_env"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError("user_agent_env must name an environment variable")
        name = name.strip()
        value = os.environ.get(name)
        if value is None or not value.strip():
            raise ValueError(f"Required User-Agent environment variable is missing or empty: {name}")
    else:
        value = crawler_settings["user_agent"]

    if not isinstance(value, str) or not value.strip():
        raise ValueError("Configured User-Agent must be a non-empty string")
    if "\r" in value or "\n" in value:
        raise ValueError("Configured User-Agent must be a single line")
    return value.strip()


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


def _find_missing_details(
    expected_details: list[dict],
    records: list[dict],
) -> tuple[int, list[dict]]:
    """Compare queued Watchlist detail pages with the records they produced."""

    expected = {
        str(item["record_id"]): item
        for item in expected_details
    }

    produced = {
        str(record.get("source_record_id"))
        for record in records
    }

    return (
        len(expected),
        [
            item
            for record_id, item in expected.items()
            if record_id not in produced
        ],
    )


def _keep_broken_as_list_only(
    expected_details: list[dict],
    records: list[dict],
) -> list[dict]:
    """Add a list-only record for each detail page broken on the source; return those pages."""

    produced = {
        str(record.get("source_record_id"))
        for record in records
    }

    broken = [
        item
        for item in expected_details
        if item.get("permanent")
        and str(item["record_id"]) not in produced
    ]

    for item in broken:
        logger.warning(
            "Detail page broken on source, keeping list data only. url=%s error=%s",
            item["detail_url"],
            item.get("error"),
        )
        records.append(
            {
                "source_record_id": item["record_id"],
                "list": dict(item.get("list_data") or {}),
                "detail": {},
                "attachments": [],
                "detail_url": item["detail_url"],
                "detail_error": item.get("error"),
            }
        )

    return broken


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

    Optional crawler_settings (throttle/retry) come from
    the source config; the Media retry backoff middleware
    applies only to Media spiders.
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

            if not task.source_file_path and not task.detail_items:
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
    expected_details = []

    # Direct next-link listings must reach their final page.
    # Detail-only retries and other acquisition modes retain their behavior.
    pagination_state = None
    if (
        not is_media
        and spider_class is GenericSpider
        and str(
            crawler_config.get("pagination", {}).get("type", "none")
        ).strip().lower() == "next_link"
        and not getattr(task, "detail_items", None)
    ):
        pagination_state = {"complete": False, "error": None}

    # =====================================================
    # 6. SCRAPY SETTINGS
    # =====================================================

    settings = get_project_settings()

    settings.set(
        "LOG_LEVEL",
        "INFO",
    )

    crawler_settings = (
        acquisition_config if is_media else crawler_config
    ).get("crawler_settings", {})
    user_agent = _resolve_user_agent(crawler_settings)

    settings.set(
        "USER_AGENT",
        user_agent or (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/126.0.0.0 "
            "Safari/537.36"
        ),
    )

    # =====================================================
    # 7. PER-SOURCE HTTP SETTINGS
    # =====================================================

    if crawler_settings:

        # ---------------------------------------------
        # Concurrent requests
        # ---------------------------------------------

        if (
            "concurrent_requests_per_domain"
            in crawler_settings
        ):
            settings.set(
                "CONCURRENT_REQUESTS_PER_DOMAIN",
                int(
                    crawler_settings[
                        "concurrent_requests_per_domain"
                    ]
                ),
            )

        # ---------------------------------------------
        # Download delay
        # ---------------------------------------------

        if (
            "download_delay"
            in crawler_settings
        ):
            settings.set(
                "DOWNLOAD_DELAY",
                float(
                    crawler_settings[
                        "download_delay"
                    ]
                ),
            )

        # ---------------------------------------------
        # Randomized delay
        # ---------------------------------------------

        if (
            "randomize_download_delay"
            in crawler_settings
        ):
            settings.set(
                "RANDOMIZE_DOWNLOAD_DELAY",
                bool(
                    crawler_settings[
                        "randomize_download_delay"
                    ]
                ),
            )

        # ---------------------------------------------
        # Retry
        # ---------------------------------------------

        settings.set(
            "RETRY_ENABLED",
            True,
        )

        if (
            "retry_times"
            in crawler_settings
        ):
            settings.set(
                "RETRY_TIMES",
                int(
                    crawler_settings[
                        "retry_times"
                    ]
                ),
            )

        if (
            "retry_http_codes"
            in crawler_settings
        ):
            settings.set(
                "RETRY_HTTP_CODES",
                list(
                    crawler_settings[
                        "retry_http_codes"
                    ]
                ),
            )

    # ---------------------------------------------
    # Media retry backoff
    # ---------------------------------------------

    if is_media:

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
        watchlist_options = {
            "task": task,
            "crawler_config": crawler_config,
            "storage": storage,
            "records": records,
            "expected_details": expected_details,
        }
        if pagination_state is not None:
            watchlist_options["pagination_state"] = pagination_state

        process.crawl(spider_class, **watchlist_options)

    # =====================================================
    # 10. START
    # =====================================================

    process.start()

    if pagination_state is not None:
        if pagination_state.get("error") or not pagination_state.get("complete"):
            reason = (
                pagination_state.get("error")
                or "The final listing page was not reached."
            )
            raise RuntimeError(
                f"Incomplete listing pagination: {reason}"
            )

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

    missing_details = []
    broken_details = []

    if not is_media:
        broken_details = _keep_broken_as_list_only(
            expected_details=expected_details,
            records=records,
        )

        (
            selected_detail_count,
            missing_details,
        ) = _find_missing_details(
            expected_details=expected_details,
            records=records,
        )

        missing_detail_count = len(
            missing_details
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
        missing_details=missing_details,
        broken_details=broken_details,
    )
