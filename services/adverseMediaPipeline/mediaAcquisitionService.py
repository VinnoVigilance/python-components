import logging
import hashlib
import json

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from typing import Any

from infrastructure.database.connection import (
    connection_pool,
)

from ingestion.apiCollector.interface import (
    ApiCollectorTask,
    collect_artifacts,
)

from ingestion.bypassCollector import (
    BypassCollector,
)

from ingestion.crawler.interface import (
    crawl,
)

from ingestion.crawler.models import (
    CrawlerTask,
)

from repositories.adverseMedia import (
    mediaRepository,
)

from services.adverseMediaPipeline.mediaDiscoveryService import (
    MediaDiscoveryService,
)

from services.adverseMediaPipeline.mediaIdentityService import (
    MediaIdentityService,
)

from services.adverseMediaPipeline.mediaFileService import (
    MediaFileService,
)

from services.adverseMediaPipeline import (
    mediaTodoService,
)

from services.adverseMediaPipeline.mediaFileStorageService import (
    MediaRawService,
)
from utils.jsonlRecordBuffer import JsonlRecordBuffer


logger = logging.getLogger(__name__)


ROOT_DIR = Path(
    __file__
).resolve().parents[2]


@dataclass
class MediaAcquisitionResult:
    source_id: int
    dataset_id: int

    discovered_count: int
    stored_count: int
    duplicate_count: int
    failed_count: int

    acquired_count: int = 0
    known_count: int = 0
    new_count: int = 0
    selected_detail_count: int = 0
    missing_detail_count: int = 0
    discovery_failed_count: int = 0
    discovery_retry_items: list[dict[str, Any]] = field(default_factory=list)
    identity_failure_count: int = 0
    reached_source_end: bool | None = None
    stop_reason: str = "UNEXPECTED_STOP"
    completed_safely: bool = False

    records: list[dict[str, Any]] = field(
        default_factory=list
    )
    record_spool_path: str | None = None
    record_spool_manifest_path: str | None = None
    processed_spool_path: str | None = None


@dataclass
class MediaSourceAcquisitionResult:
    """Source-level acquisition result before Raw persistence."""

    records: list[dict[str, Any]] = field(
        default_factory=list
    )
    discovered_count: int = 0
    known_count: int = 0
    new_count: int = 0
    selected_detail_count: int = 0
    missing_detail_count: int = 0
    discovery_failed_count: int = 0
    discovery_retry_items: list[dict[str, Any]] = field(default_factory=list)
    identity_failure_count: int = 0
    reached_source_end: bool | None = None
    stop_reason: str = "UNEXPECTED_STOP"
    completed_safely: bool = False
    record_spool_path: str | None = None
    record_spool_manifest_path: str | None = None

def collect_rendered_article_links(read_listing, scroll_to_more, wait_for_batch):
    """Collect unique links until the listing ends or progress times out."""
    urls = {}
    batch_links, reached_end = read_listing()

    while True:
        urls.update(dict.fromkeys(batch_links))
        if reached_end:
            return list(urls), True

        scroll_to_more()
        next_batch = wait_for_batch(set(urls))
        if next_batch is None:
            return list(urls), False

        batch_links, reached_end = next_batch


class MediaAcquisitionService:
    """
    Orchestrates the complete Media Acquisition stage.

    Flow:

        Resolve lookup values
        -> Acquire through Crawler or API
        -> Save local raw file
        -> Build file metadata
        -> Calculate file_hash
        -> Duplicate check
        -> SeaweedFS
        -> raw.media_file

    Processing and normalization are not part
    of this service.
    """

    def acquire(
        self,
        source_config: dict[str, Any],
        mode: str,
    ) -> MediaAcquisitionResult:

        if mode not in {
            "INITIAL",
            "INCREMENTAL",
            "RETRY",
        }:
            raise ValueError(
                "Media acquisition mode must be "
                "INITIAL, INCREMENTAL or RETRY."
            )

        mode_config = source_config.get("discovery_by_mode", {}).get(mode)
        if mode_config:
            discovery_settings = {
                key: value
                for key, value in mode_config.items()
                if key not in {"acquisition", "bypass_config"}
            }
            source_config = {
                **source_config,
                "discovery": {
                    **source_config.get("discovery", {}),
                    **discovery_settings,
                },
                "acquisition": {
                    **source_config.get("acquisition", {}),
                    **mode_config.get("acquisition", {}),
                },
            }
            if "bypass_config" in mode_config:
                source_config["bypass_config"] = mode_config["bypass_config"]
        # =================================================
        # 1. Validate source configuration
        # =================================================

        self._validate_source_config(
            source_config
        )

        source_name = source_config[
            "source_name"
        ]

        dataset_name = source_config[
            "dataset_name"
        ]

        source_url = source_config[
            "url"
        ]

        acquisition_config = (
            source_config.get(
                "acquisition",
                {},
            )
        )

        acquisition_type = (
            self._get_acquisition_type(
                source_config
            )
        )

        download_method = (
            str(
                acquisition_config.get(
                    "download_method",
                    acquisition_type,
                )
            )
            .strip()
            .upper()
        )

        # =================================================
        # 2. Resolve source_id / dataset_id
        # =================================================

        lookup_values = (
            self._resolve_lookup_values(
                source_name=source_name,
                dataset_name=dataset_name,
            )
        )

        source_id = lookup_values[
            "source_id"
        ]

        dataset_id = lookup_values[
            "dataset_id"
        ]

        # =================================================
        # 3. Read discovery policy
        # =================================================

        discovery_config = (
            source_config.get(
                "discovery",
                {},
            )
        )

        discovery_policy = str(
            discovery_config.get(
                "policy",
                "",
            )
        ).strip().lower()

        if discovery_policy not in {
            "full_scan",
            "stop_after_known",
        }:
            raise ValueError(
                "discovery.policy must be "
                "full_scan or stop_after_known."
            )

        known_threshold = discovery_config.get(
            "threshold"
        )

        if discovery_policy == "stop_after_known":
            if known_threshold is None:
                raise ValueError(
                    "discovery.threshold is required "
                    "when policy=stop_after_known."
                )

            known_threshold = int(
                known_threshold
            )

            if known_threshold <= 0:
                raise ValueError(
                    "discovery.threshold must be "
                    "greater than zero."
                )

        else:
            # The threshold has no meaning for a full scan.
            known_threshold = 1

        # =================================================
        # 4. Acquire source files
        # =================================================

        if mode == "RETRY":
            if (source_config.get("discovery", {}).get("strategy") == "list_only"
                    and any(item.get("kind") == "list_only"
                            for item in mediaTodoService.load_items(dataset_name))):
                # Row positions are not replayable URLs. Refresh the index using
                # its normal acquisition path, including browser clearing.
                source_result = self._acquire_source(
                    source_config=source_config, acquisition_type=acquisition_type,
                    source_id=source_id, dataset_id=dataset_id, mode="INCREMENTAL",
                    discovery_policy="full_scan", known_threshold=known_threshold,
                )
            else:
                source_result = self._acquire_todo_items(source_config=source_config)

        else:
            source_result = (
                self._acquire_source(
                    source_config=source_config,
                    acquisition_type=acquisition_type,
                    source_id=source_id,
                    dataset_id=dataset_id,
                    mode=mode,
                    discovery_policy=discovery_policy,
                    known_threshold=known_threshold,
                )
            )

        acquired_records = source_result.records

        # =================================================
        # 5. Persist every acquired detail file
        # =================================================

        raw_workers = int(acquisition_config.get("crawler_settings", {}).get("raw_workers", 1))
        if raw_workers < 1 or raw_workers > 8:
            raise ValueError("crawler_settings.raw_workers must be between 1 and 8")

        processed_spool_path = None
        if source_result.record_spool_path:
            processed_spool_path = str(
                Path(source_result.record_spool_path).with_name("records.processed.jsonl")
            )
            processed_results = JsonlRecordBuffer(processed_spool_path, reset=True)
        else:
            processed_results = []

        stored_count = 0
        duplicate_count = 0
        failed_count = (
            source_result.discovery_failed_count
            + source_result.missing_detail_count
        )
        record_failed_count = 0
        persistence_total = len(acquired_records)
        persistence_started_at = perf_counter()

        def persist_one(record):
            if record.get("discovery_retry_complete"):
                return record, "control"
            if record.get("failed"):
                failed = dict(record)
                failed.setdefault("error_stage", "DETAIL_FETCH")
                failed.setdefault("error_type", "DetailFetchError")
                return failed, "failed"
            try:
                return self._persist_record(
                    record=record,
                    source_id=source_id,
                    dataset_id=dataset_id,
                    source_name=source_name,
                    dataset_name=dataset_name,
                    download_method=download_method,
                    acquisition_url=source_url,
                ), "persisted"
            except Exception as error:
                logger.exception(
                    "Media acquisition persistence failed. record_key=%s",
                    record.get("record_key"),
                )
                return {
                    "record_key": record.get("record_key"),
                    "is_known": record.get("is_known"),
                    "extracted": record.get("extracted"),
                    "detail_file_path": record.get("detail_file_path"),
                    "failed": True,
                    "error": str(error),
                    "error_stage": "RAW_PERSISTENCE",
                    "error_type": type(error).__name__,
                    "discovery_retry_key": record.get("discovery_retry_key"),
                    "discovery_context": record.get("discovery_context"),
                    "discovery_retry_item": record.get("discovery_retry_item"),
                    "retry_listing": record.get("retry_listing", False),
                    "attachment_manifest": record.get("attachment_manifest", False),
                }, "failed"

        executor = ThreadPoolExecutor(max_workers=raw_workers) if raw_workers > 1 else None

        def persist_records():
            if executor is None:
                yield from map(persist_one, acquired_records)
                return
            iterator = iter(acquired_records)
            while True:
                batch = list(islice(iterator, raw_workers * 4))
                if not batch:
                    return
                yield from executor.map(persist_one, batch)

        persisted_records = persist_records()

        for record_index, (processed_record, outcome) in enumerate(persisted_records, start=1):

            try:
                processed_results.append(processed_record)
                if outcome == "control":
                    continue
                if outcome == "failed":
                    failed_count += 1
                    record_failed_count += 1
                    continue

                if processed_record[
                    "is_duplicate"
                ]:
                    duplicate_count += 1

                else:
                    stored_count += 1

            finally:
                if record_index % 100 == 0 or record_index == persistence_total:
                    elapsed = max(perf_counter() - persistence_started_at, 0.001)
                    logger.info(
                        "Media Raw persistence progress. "
                        "dataset=%s processed=%s total=%s stored=%s "
                        "duplicates=%s failed=%s remaining=%s "
                        "elapsed_seconds=%.2f records_per_second=%.2f",
                        dataset_name,
                        record_index,
                        persistence_total,
                        stored_count,
                        duplicate_count,
                        record_failed_count,
                        persistence_total - record_index,
                        elapsed,
                        record_index / elapsed,
                    )

        if executor is not None:
            executor.shutdown(wait=True)
        if isinstance(acquired_records, JsonlRecordBuffer):
            acquired_records.close()
        if isinstance(processed_results, JsonlRecordBuffer):
            processed_results.close()
            processed_results = JsonlRecordBuffer(processed_spool_path)

        # =================================================
        # 6. Return acquisition result
        # =================================================

        return MediaAcquisitionResult(
            source_id=source_id,
            dataset_id=dataset_id,

            discovered_count=(
                source_result.discovered_count
            ),

            stored_count=stored_count,
            duplicate_count=duplicate_count,
            failed_count=failed_count,

            acquired_count=len(
                acquired_records
            ),
            known_count=source_result.known_count,
            new_count=source_result.new_count,
            selected_detail_count=(
                source_result.selected_detail_count
            ),
            missing_detail_count=(
                source_result.missing_detail_count
            ),
            discovery_failed_count=(
                source_result.discovery_failed_count
            ),
            discovery_retry_items=source_result.discovery_retry_items,
            identity_failure_count=(
                source_result.identity_failure_count
            ),
            reached_source_end=(
                source_result.reached_source_end
            ),
            stop_reason=source_result.stop_reason,
            completed_safely=(
                source_result.completed_safely
            ),

            records=processed_results,
            record_spool_path=source_result.record_spool_path,
            record_spool_manifest_path=source_result.record_spool_manifest_path,
            processed_spool_path=processed_spool_path,
        )

    # =====================================================
    # Lookup
    # =====================================================

    @staticmethod
    def _resolve_lookup_values(
        source_name: str,
        dataset_name: str,
    ) -> dict[str, int]:

        connection = (
            connection_pool.getconn()
        )

        try:
            with connection:
                with connection.cursor() as cursor:

                    source_id = (
                        mediaRepository
                        .find_media_source_id(
                            cursor=cursor,
                            source_name=source_name,
                        )
                    )

                    if source_id is None:
                        raise ValueError(
                            "Media source not found: "
                            f"{source_name}"
                        )

                    dataset_id = (
                        mediaRepository
                        .find_media_dataset_id(
                            cursor=cursor,
                            dataset_name=dataset_name,
                            source_id=source_id,
                        )
                    )

                    if dataset_id is None:
                        raise ValueError(
                            "Media dataset not found: "
                            f"{dataset_name} "
                            "for source "
                            f"{source_name}"
                        )

                    return {
                        "source_id": source_id,
                        "dataset_id": dataset_id,
                    }

        finally:
            connection_pool.putconn(
                connection
            )

    # =====================================================
    # Acquire
    # =====================================================

    @staticmethod
    def _acquire_source(
        source_config: dict[str, Any],
        acquisition_type: str,
        source_id: int,
        dataset_id: int,
        mode: str,
        discovery_policy: str,
        known_threshold: int,
    ) -> MediaSourceAcquisitionResult:

        if acquisition_type == "crawler":

            crawl_result = (
                MediaAcquisitionService
                ._crawl_source(
                    source_config=source_config,
                    source_id=source_id,
                    dataset_id=dataset_id,
                    stop_after_known=(
                        mode == "INCREMENTAL"
                        and discovery_policy
                        == "stop_after_known"
                    ),
                    known_threshold=(
                        known_threshold
                    ),
                    source_name=source_config[
                        "source_name"
                    ],
                    dataset_name=source_config[
                        "dataset_name"
                    ],
                    source_url=source_config[
                        "url"
                    ],
                    mode=mode,
                )
            )

            discovered_count = getattr(
                crawl_result,
                "discovered_count",
                None,
            )

            if discovered_count is None:
                discovered_count = len(
                    crawl_result.records
                )

            return MediaSourceAcquisitionResult(
                records=crawl_result.records,
                discovered_count=discovered_count,
                known_count=getattr(
                    crawl_result,
                    "known_count",
                    0,
                ),
                new_count=getattr(
                    crawl_result,
                    "new_count",
                    0,
                ),
                selected_detail_count=getattr(
                    crawl_result,
                    "selected_detail_count",
                    0,
                ),
                missing_detail_count=getattr(
                    crawl_result,
                    "missing_detail_count",
                    0,
                ),
                discovery_failed_count=getattr(
                    crawl_result,
                    "discovery_failure_count",
                    getattr(
                        crawl_result,
                        "identity_failure_count",
                        0,
                    ),
                ),
                discovery_retry_items=getattr(crawl_result, "discovery_retry_items", []),
                identity_failure_count=getattr(
                    crawl_result,
                    "identity_failure_count",
                    0,
                ),
                reached_source_end=getattr(
                    crawl_result,
                    "reached_source_end",
                    None,
                ),
                stop_reason=(
                    getattr(
                        crawl_result,
                        "stop_reason",
                        None,
                    )
                    or "UNEXPECTED_STOP"
                ),
                completed_safely=bool(
                    getattr(
                        crawl_result,
                        "completed_safely",
                        False,
                    )
                ),
                record_spool_path=getattr(crawl_result, "record_spool_path", None),
                record_spool_manifest_path=getattr(
                    crawl_result, "record_spool_manifest_path", None
                ),
            )

        if acquisition_type == "api":

            return (
                MediaAcquisitionService
                ._collect_api_source(
                    source_config=source_config,
                    source_id=source_id,
                    dataset_id=dataset_id,
                    stop_after_known=(
                        mode == "INCREMENTAL"
                        and discovery_policy
                        == "stop_after_known"
                    ),
                    known_threshold=known_threshold,
                )
            )

        raise ValueError(
            "Unsupported Media acquisition type: "
            f"{acquisition_type}"
        )

    @staticmethod
    def _acquire_todo_items(
        source_config: dict[str, Any],
    ) -> MediaSourceAcquisitionResult:
        """Rebuild records for to-do items: saved files as-is, failed pages fetched again."""

        dataset_name = source_config["dataset_name"]
        items = mediaTodoService.load_items(dataset_name)

        records = [
            {
                "record_key": item.get("record_key"),
                "is_known": False,
                "extracted": item.get("extracted"),
                "detail_file_path": item["detail_file_path"],
                "discovery_context": item.get("discovery_context"),
            }
            for item in items
            if item["kind"] == "process" and (
                Path(item["detail_file_path"]).is_file() or not item.get("discovery_context"))
        ]

        fetch_items = [
            item
            for item in items
            if item["kind"] == "fetch"
        ]

        discovery_items = [item for item in items if item["kind"] in {"json_manifest_entry", "month_index", "form_search"}]
        discovery_items.extend(item["discovery_context"] for item in items
                               if item["kind"] == "process" and item.get("discovery_context")
                               and not Path(item["detail_file_path"]).is_file())

        crawl_completed = True
        record_spool_path = None
        record_spool_manifest_path = None
        if fetch_items or discovery_items:
            fetch_strategy = str(
                source_config.get(
                    "acquisition",
                    {},
                ).get(
                    "fetch_strategy",
                    "direct",
                )
            ).strip().lower()

            crawl_result = crawl(
                task=CrawlerTask(
                    url=source_config["url"],
                    source_name=source_config["source_name"],
                    list_name=dataset_name,
                    source_config=source_config,
                    fetch_strategy=fetch_strategy,
                    download_dir=str(
                        ROOT_DIR
                        / "data"
                        / "downloads"
                    ),
                    detail_items=fetch_items,
                    discovery_items=discovery_items,
                    spool_scope="retry",
                ),
            )

            records.extend(
                crawl_result.records
            )
            close_crawl_records = getattr(crawl_result.records, "close", None)
            if callable(close_crawl_records):
                close_crawl_records()
            crawl_completed = bool(getattr(crawl_result, "completed_safely", True))
            record_spool_path = getattr(crawl_result, "record_spool_path", None)
            record_spool_manifest_path = getattr(
                crawl_result, "record_spool_manifest_path", None
            )

        if any(item.get("kind") == "form_search" for item in discovery_items):
            records = list({record["record_key"]: record for record in records}.values())

        return MediaSourceAcquisitionResult(
            records=records,
            discovered_count=len(items),
            new_count=len(items),
            selected_detail_count=len(records),
            missing_detail_count=max(sum(item["kind"] != "form_search" for item in items) - len(records), 0),
            reached_source_end=True,
            stop_reason="TODO_RETRY",
            completed_safely=(len(records) >= sum(item["kind"] != "form_search" for item in items)
                              and crawl_completed
                              and not any(record.get("failed") for record in records)),
            record_spool_path=record_spool_path,
            record_spool_manifest_path=record_spool_manifest_path,
        )

    @staticmethod
    def _crawl_source(
        source_config: dict[str, Any],
        source_id: int,
        dataset_id: int,
        stop_after_known: bool,
        known_threshold: int,
        source_name: str,
        dataset_name: str,
        source_url: str,
        mode: str = "INITIAL",
    ):
        """
        Acquire a crawler-based Media source.

        direct:
            MediaSpider opens listing and detail pages.

        saved_html:
            BypassCollector saves the protected listing.
            SavedHtmlMediaSpider reads that listing and
            opens each detail page through the browser.
        """

        acquisition_config = (
            source_config.get(
                "acquisition",
                {},
            )
        )

        fetch_strategy = str(
            acquisition_config.get(
                "fetch_strategy",
                "direct",
            )
        ).strip().lower()

        source_file_path = None
        saved_listing_files = {}
        failed_listing_urls = []

        if fetch_strategy == "saved_html":
            listing_urls = source_config.get("discovery", {}).get("archive_urls")

            if listing_urls:
                for listing_url in dict.fromkeys(listing_urls):
                    try:
                        saved_listing_files[listing_url] = (
                            MediaAcquisitionService._collect_bypass_listing(
                                source_config={**source_config, "url": listing_url},
                                dataset_name=dataset_name,
                            )
                        )
                    except (RuntimeError, OSError):
                        logger.warning(
                            "Could not save Media listing: %s",
                            listing_url,
                            exc_info=True,
                        )
                        failed_listing_urls.append(listing_url)

                if not saved_listing_files:
                    raise RuntimeError(
                        f"No Media archive listings were saved for {dataset_name}."
                    )
            else:
                source_file_path = MediaAcquisitionService._collect_bypass_listing(
                    source_config=source_config,
                    dataset_name=dataset_name,
                )

        elif fetch_strategy != "direct":
            raise ValueError(f"Unsupported Media fetch_strategy: {fetch_strategy}")


        # Bypass collection does not need a DB connection.
        # Obtain the connection only after the listing
        # has been saved.
        connection = (
            connection_pool.getconn()
        )

        try:
            with connection.cursor() as cursor:

                discovery_service = (
                    MediaDiscoveryService(
                        cursor=cursor,
                        source_id=source_id,
                        dataset_id=dataset_id,
                        source_config=(
                            source_config
                        ),
                        stop_after_known=(
                            stop_after_known
                        ),
                        known_threshold=(
                            known_threshold
                        ),
                    )
                )

                for listing_url in failed_listing_urls:
                    discovery_service.mark_discovery_failure(
                        f"LISTING_COLLECTION_FAILED: {listing_url}"
                    )


                task = CrawlerTask(
                    url=source_url,
                    source_name=source_name,
                    list_name=dataset_name,

                    source_config=(
                        source_config
                    ),

                    fetch_strategy=(
                        fetch_strategy
                    ),

                    source_file_path=(
                        source_file_path
                    ),
                    saved_listing_files=saved_listing_files,

                    download_dir=str(
                        ROOT_DIR
                        / "data"
                        / "downloads"
                    ),
                    spool_scope=mode.lower(),
                )

                return crawl(
                    task=task,
                    discovery_service=(
                        discovery_service
                    ),
                )

        finally:
            connection_pool.putconn(
                connection
            )

    @staticmethod
    def _collect_bypass_listing(
        source_config: dict[str, Any],
        dataset_name: str,
    ) -> str:
        """
        Save a protected Media listing page through
        BypassCollector.

        The listing is an intermediate discovery
        artifact. It is not registered as an
        individual Media file in raw.media_file.
        """

        # BypassCollector uses list_name for its
        # output path. Media uses dataset_name.
        bypass_source_config = {
            **source_config,
            "list_name": dataset_name,
        }

        collector = BypassCollector(
            outputDir=(
                ROOT_DIR
                / "data"
                / "downloads"
            )
        )

        collected_path = collector.collect(
            bypass_source_config
        )

        if collected_path is None:
            raise RuntimeError(
                "Bypass collection failed for "
                f"{source_config.get('source_name')}/"
                f"{dataset_name}."
            )

        listing_path = Path(
            collected_path
        ).resolve()

        if not listing_path.is_file():
            raise FileNotFoundError(
                "Bypass listing artifact was not "
                f"found: {listing_path}"
            )

        return str(
            listing_path
        )

    @staticmethod
    def _collect_api_source(
        source_config: dict[str, Any],
        source_id: int,
        dataset_id: int,
        stop_after_known: bool,
        known_threshold: int,
    ) -> MediaSourceAcquisitionResult:
        """
        Acquire an API-based Media source; with stop_after_known, stop paging
        once known_threshold already-saved records appear in a row.
        """

        if not stop_after_known:

            collection_result = (
                collect_artifacts(
                    ApiCollectorTask.from_config(
                        source_config
                    )
                )
            )

            records = [
                {
                    "detail_file_path": file_path,
                }
                for file_path
                in collection_result.file_paths
            ]

            return MediaSourceAcquisitionResult(
                records=records,
                discovered_count=collection_result.record_count,
                new_count=collection_result.record_count,
                selected_detail_count=len(records),
                reached_source_end=True,
                stop_reason="SOURCE_EXHAUSTED",
                completed_safely=True,
            )

        connection = (
            connection_pool.getconn()
        )

        try:
            with connection.cursor() as cursor:

                discovery_service = (
                    MediaDiscoveryService(
                        cursor=cursor,
                        source_id=source_id,
                        dataset_id=dataset_id,
                        source_config=(
                            source_config
                        ),
                        stop_after_known=True,
                        known_threshold=(
                            known_threshold
                        ),
                    )
                )

                collection_result = (
                    collect_artifacts(
                        ApiCollectorTask.from_config(
                            source_config
                        ),
                        stop_check=(
                            MediaAcquisitionService
                            ._build_api_stop_check(
                                discovery_service=(
                                    discovery_service
                                ),
                                source_config=(
                                    source_config
                                ),
                            )
                        ),
                    )
                )

                records = [
                    {
                        "detail_file_path": file_path,
                    }
                    for file_path
                    in collection_result.file_paths
                ]

                if discovery_service.stop_reason is None:
                    discovery_service.mark_source_end()

                summary = discovery_service.get_summary()

                return MediaSourceAcquisitionResult(
                    records=records,
                    discovered_count=summary["discovered_count"],
                    known_count=summary["known_count"],
                    new_count=summary["new_count"],
                    selected_detail_count=len(records),
                    discovery_failed_count=(
                        summary["discovery_failure_count"]
                    ),
                    identity_failure_count=(
                        summary["identity_failure_count"]
                    ),
                    reached_source_end=summary["reached_source_end"],
                    stop_reason=summary["stop_reason"],
                    completed_safely=summary["completed_safely"],
                )

        finally:
            connection_pool.putconn(
                connection
            )

    @staticmethod
    def _build_api_stop_check(
        discovery_service: MediaDiscoveryService,
        source_config: dict[str, Any],
    ):
        """
        Build the callback apiCollector runs after each page.

        Walks the page's records in order (the API's own order, not
        re-sorted by us) and returns True as soon as known_threshold
        already-saved records have appeared in a row -- the signal
        that every remaining record, on this page and any later page,
        is older than what we already have.
        """

        def stop_check(
            items: list[dict[str, Any]],
        ) -> bool:

            for item in items:

                try:
                    record_key = (
                        MediaIdentityService
                        .generate_record_key(
                            source_config=(
                                source_config
                            ),
                            record=item,
                        )
                    )

                except ValueError as exc:

                    logger.warning(
                        "Could not build media identity "
                        "for API record: %s",
                        exc,
                    )

                    continue

                try:
                    (
                        _,
                        should_stop,
                    ) = (
                        discovery_service
                        .check_record_key(
                            record_key
                        )
                    )

                except Exception:

                    logger.exception(
                        "Could not check media record "
                        "key=%s",
                        record_key,
                    )

                    continue

                if should_stop:
                    return True

            return False

        return stop_check

    # =====================================================
    # Persist one acquired record
    # =====================================================

    @staticmethod
    def _persist_record(
        record: dict[str, Any],
        source_id: int,
        dataset_id: int,
        source_name: str,
        dataset_name: str,
        download_method: str,
        acquisition_url: str,
    ) -> dict[str, Any]:

        detail_file_path = record.get(
            "detail_file_path"
        )

        if not detail_file_path:
            raise ValueError(
                "detail_file_path is missing "
                "from Media acquisition result."
            )

        # Crawler records may already contain extracted fields, while API
        # records contain only a path to their individual JSON file. Preserve
        # None for API records so MediaRawRecordService parses that file.
        extracted = record.get(
            "extracted"
        )

        if record.get("attachment_manifest") and extracted is not None:
            # Register each original attachment through the existing durable-file
            # path, then save a portable manifest as the row's primary original.
            # Local filenames must never become the only attachment references.
            for document in extracted.get("DocumentFiles", []):
                try:
                    metadata = MediaFileService.build_file_metadata(
                        file_path=document["local_path"], file_url=document["url"])
                    stored = MediaRawService.register_file(
                        source_id=source_id, dataset_id=dataset_id,
                        source_name=source_name, dataset_name=dataset_name,
                        file_metadata=metadata, download_method=download_method,
                    )
                    document.update({key: stored[key] for key in
                                     ("media_file_id", "file_hash", "storage_path")})
                except Exception as error:
                    extracted.setdefault("DocumentErrors", []).append(
                        {"url": document.get("url"), "stage": "storage", "error": str(error)})
            portable = {key: value for key, value in extracted.items()
                        if key not in {"DocumentFiles", "DocumentFilePaths"}}
            portable["SourceRowHTML"] = Path(detail_file_path).read_text(encoding="utf-8")
            portable["DocumentFiles"] = [
                {key: value for key, value in document.items() if key != "local_path"}
                for document in extracted.get("DocumentFiles", [])]
            payload = json.dumps(portable, ensure_ascii=False, sort_keys=True).encode("utf-8")
            manifest = Path(detail_file_path).with_name(hashlib.sha256(payload).hexdigest() + ".json")
            temporary = None
            try:
                with NamedTemporaryFile(dir=manifest.parent, suffix=".tmp", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(payload)
                temporary.replace(manifest)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            detail_file_path = str(manifest)

        source_url = (
            ((extracted or {}).get("SourceURL") if record.get("attachment_manifest") else None)
            or record.get("file_url")
            or (extracted or {}).get("PDFURL")
            or (extracted or {}).get(
                "SourceURL"
            )
            or acquisition_url
        )

        # ---------------------------------------------
        # metadata + file_hash
        # ---------------------------------------------

        file_metadata = (
            MediaFileService
            .build_file_metadata(
                file_path=detail_file_path,
                file_url=source_url,
            )
        )

        # ---------------------------------------------
        # duplicate + SeaweedFS + Raw DB
        # ---------------------------------------------

        raw_result = (
            MediaRawService
            .register_file(
                source_id=source_id,
                dataset_id=dataset_id,
                source_name=source_name,
                dataset_name=dataset_name,
                file_metadata=file_metadata,
                download_method=download_method,
            )
        )

        return {
            "record_key": record.get(
                "record_key"
            ),

            "is_known": record.get(
                "is_known"
            ),

            "extracted": extracted,

            "detail_file_path": (
                detail_file_path
            ),

            "media_file_id": (
                raw_result[
                    "media_file_id"
                ]
            ),

            "file_hash": (
                raw_result[
                    "file_hash"
                ]
            ),

            "storage_path": (
                raw_result[
                    "storage_path"
                ]
            ),

            "raw_status": (
                raw_result[
                    "status"
                ]
            ),

            "is_duplicate": (
                raw_result[
                    "is_duplicate"
                ]
            ),

            "failed": False,
            "retry_listing": record.get("retry_listing", False),
            "discovery_retry_key": record.get("discovery_retry_key"),
            "discovery_context": record.get("discovery_context"),
            "discovery_retry_item": record.get("discovery_retry_item"),
        }

    # =====================================================
    # Config validation
    # =====================================================

    @staticmethod
    def _get_acquisition_type(
        source_config: dict[str, Any],
    ) -> str:

        acquisition_config = (
            source_config.get(
                "acquisition",
                {},
            )
        )

        return (
            str(
                acquisition_config.get(
                    "type",
                    "",
                )
            )
            .strip()
            .lower()
        )

    @staticmethod
    def _validate_source_config(
        source_config: dict[str, Any],
    ) -> None:

        required_fields = [
            "source_name",
            "dataset_name",
            "url",
        ]

        for field_name in required_fields:

            value = source_config.get(
                field_name
            )

            if (
                value is None
                or str(value).strip() == ""
            ):
                raise ValueError(
                    "Missing required Media "
                    "configuration: "
                    f"{field_name}"
                )

        acquisition_config = (
            source_config.get(
                "acquisition",
                {},
            )
        )

        acquisition_type = (
            MediaAcquisitionService
            ._get_acquisition_type(
                source_config
            )
        )

        if acquisition_type not in {
            "crawler",
            "api",
        }:
            raise ValueError(
                "MediaAcquisitionService "
                "supports acquisition.type "
                "crawler or api."
            )

        if acquisition_type == "api":

            api_config = source_config.get(
                "api_config",
                {},
            )

            if not api_config:
                raise ValueError(
                    "api_config is required for "
                    "API-based Media sources."
                )

            if api_config.get(
                "write_mode"
            ) != "record_files":
                raise ValueError(
                    "API-based Media sources must use "
                    "api_config.write_mode=record_files."
                )

            return

        spider_type = (
            str(
                acquisition_config.get(
                    "spider",
                    "",
                )
            )
            .strip()
            .lower()
        )

        if spider_type != "media":
            raise ValueError(
                "Crawler-based Media source "
                "must use "
                "acquisition.spider=media."
            )

        fetch_strategy = str(
            acquisition_config.get(
                "fetch_strategy",
                "direct",
            )
        ).strip().lower()

        if fetch_strategy not in {
            "direct",
            "saved_html",
        }:
            raise ValueError(
                "Crawler-based Media source "
                "supports acquisition."
                "fetch_strategy direct or "
                "saved_html."
            )

        if fetch_strategy == "saved_html":

            bypass_config = (
                source_config.get(
                    "bypass_config",
                    {},
                )
            )

            if not bypass_config:
                raise ValueError(
                    "bypass_config is required for "
                    "Media "
                    "fetch_strategy=saved_html."
                )

            actions = bypass_config.get(
                "actions",
                [],
            )

            if not actions:
                raise ValueError(
                    "bypass_config.actions is required "
                    "for Media "
                    "fetch_strategy=saved_html."
                )
