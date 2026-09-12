import logging
from dataclasses import dataclass, field
from typing import Any

from infrastructure.database.connection import (
    connection_pool,
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

from services.adverseMediaPipeline.mediaFileService import (
    MediaFileService,
)

from services.adverseMediaPipeline.mediaFileStorageService import (
    MediaRawService,
)


logger = logging.getLogger(__name__)


@dataclass
class MediaAcquisitionResult:
    source_id: int
    dataset_id: int

    discovered_count: int
    stored_count: int
    duplicate_count: int
    failed_count: int

    records: list[dict[str, Any]] = field(
        default_factory=list
    )


class MediaAcquisitionService:
    """
    Orchestrates the complete Media Acquisition stage.

    Flow:

        Resolve lookup values
        -> Discovery
        -> Crawl
        -> Save local HTML
        -> Build file metadata
        -> Calculate file_hash
        -> Duplicate check
        -> SeaweedFS
        -> raw.media_file

    Processing / normalization is NOT part
    of this service.
    """

    def acquire(
        self,
        source_config: dict[str, Any],
    ) -> MediaAcquisitionResult:

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

        download_method = (
            str(
                acquisition_config.get(
                    "download_method",
                    "CRAWLER",
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
        # 3. Read discovery threshold
        # =================================================

        discovery_config = (
            source_config.get(
                "discovery",
                {},
            )
        )

        stop_condition = (
            discovery_config.get(
                "stop_condition",
                {},
            )
        )

        known_threshold = int(
            stop_condition.get(
                "threshold",
                10,
            )
        )

        # =================================================
        # 4. Crawl
        # =================================================

        crawl_result = (
            self._crawl_source(
                source_config=source_config,
                source_id=source_id,
                dataset_id=dataset_id,
                known_threshold=known_threshold,
                source_name=source_name,
                dataset_name=dataset_name,
                source_url=source_url,
            )
        )

        # =================================================
        # 5. Persist every acquired file
        # =================================================

        processed_results: list[
            dict[str, Any]
        ] = []

        stored_count = 0
        duplicate_count = 0
        failed_count = 0

        for record in crawl_result.records:

            try:
                processed_record = (
                    self._persist_record(
                        record=record,
                        source_id=source_id,
                        dataset_id=dataset_id,
                        source_name=source_name,
                        dataset_name=dataset_name,
                        download_method=download_method,
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
                    }
                )

        # =================================================
        # 6. Return acquisition result
        # =================================================

        return MediaAcquisitionResult(
            source_id=source_id,
            dataset_id=dataset_id,

            discovered_count=len(
                crawl_result.records
            ),

            stored_count=stored_count,
            duplicate_count=duplicate_count,
            failed_count=failed_count,

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
                            f"Media source not found: "
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
                            f"Media dataset not found: "
                            f"{dataset_name} "
                            f"for source "
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
    # Crawl
    # =====================================================

    @staticmethod
    def _crawl_source(
        source_config: dict[str, Any],
        source_id: int,
        dataset_id: int,
        known_threshold: int,
        source_name: str,
        dataset_name: str,
        source_url: str,
    ):

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
                        source_config=source_config,
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

                    fetch_strategy="direct",
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

    # =====================================================
    # Persist one crawled record
    # =====================================================

    @staticmethod
    def _persist_record(
        record: dict[str, Any],
        source_id: int,
        dataset_id: int,
        source_name: str,
        dataset_name: str,
        download_method: str,
    ) -> dict[str, Any]:

        detail_file_path = record.get(
            "detail_file_path"
        )

        if not detail_file_path:
            raise ValueError(
                "detail_file_path is missing "
                "from MediaSpider result."
            )

        extracted = record.get(
            "extracted",
            {},
        )

        source_url = extracted.get(
            "SourceURL"
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
                    f"Missing required Media "
                    f"configuration: "
                    f"{field_name}"
                )

        acquisition_config = (
            source_config.get(
                "acquisition",
                {},
            )
        )

        acquisition_type = (
            str(
                acquisition_config.get(
                    "type",
                    "",
                )
            )
            .strip()
            .lower()
        )

        if acquisition_type != "crawler":
            raise ValueError(
                "MediaAcquisitionService "
                "currently supports "
                "acquisition.type=crawler only."
            )

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