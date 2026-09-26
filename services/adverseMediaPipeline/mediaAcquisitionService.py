import logging

from dataclasses import dataclass, field
from pathlib import Path
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

from services.adverseMediaPipeline.mediaFileStorageService import (
    MediaRawService,
)


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
    identity_failure_count: int = 0
    reached_source_end: bool | None = None
    stop_reason: str = "UNEXPECTED_STOP"
    completed_safely: bool = False

    records: list[dict[str, Any]] = field(
        default_factory=list
    )


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
    identity_failure_count: int = 0
    reached_source_end: bool | None = None
    stop_reason: str = "UNEXPECTED_STOP"
    completed_safely: bool = False


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
        }:
            raise ValueError(
                "Media acquisition mode must be "
                "INITIAL or INCREMENTAL."
            )

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

        processed_results: list[
            dict[str, Any]
        ] = []

        stored_count = 0
        duplicate_count = 0
        failed_count = (
            source_result.discovery_failed_count
            + source_result.missing_detail_count
        )

        for record in acquired_records:

            try:
                if record.get("failed"):
                    failed_count += 1
                    record.setdefault(
                        "error_stage",
                        "DETAIL_FETCH",
                    )
                    record.setdefault(
                        "error_type",
                        "DetailFetchError",
                    )
                    processed_results.append(
                        record
                    )
                    continue

                processed_record = (
                    self._persist_record(
                        record=record,
                        source_id=source_id,
                        dataset_id=dataset_id,
                        source_name=source_name,
                        dataset_name=dataset_name,
                        download_method=download_method,
                        acquisition_url=source_url,
                    )
                )

                processed_results.append(
                    processed_record
                )

                if processed_record[
                    "is_duplicate"
                ]:
                    duplicate_count += 1

                else:
                    stored_count += 1

            except Exception as error:

                failed_count += 1

                logger.exception(
                    "Media acquisition persistence failed. "
                    "record_key=%s",
                    record.get(
                        "record_key"
                    ),
                )

                processed_results.append(
                    {
                        "record_key": record.get(
                            "record_key"
                        ),
                        "is_known": record.get(
                            "is_known"
                        ),
                        "extracted": record.get(
                            "extracted"
                        ),
                        "detail_file_path": record.get(
                            "detail_file_path"
                        ),
                        "failed": True,
                        "error": str(error),
                        "error_stage": "RAW_PERSISTENCE",
                        "error_type": type(error).__name__,
                    }
                )

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
    def _crawl_source(
        source_config: dict[str, Any],
        source_id: int,
        dataset_id: int,
        stop_after_known: bool,
        known_threshold: int,
        source_name: str,
        dataset_name: str,
        source_url: str,
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

        if fetch_strategy == "saved_html":

            source_file_path = (
                MediaAcquisitionService
                ._collect_bypass_listing(
                    source_config=source_config,
                    dataset_name=dataset_name,
                )
            )

        elif fetch_strategy != "direct":

            raise ValueError(
                "Unsupported Media "
                "fetch_strategy: "
                f"{fetch_strategy}"
            )

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

                    download_dir=str(
                        ROOT_DIR
                        / "data"
                        / "downloads"
                    ),
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

        source_url = (
            (extracted or {}).get(
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
