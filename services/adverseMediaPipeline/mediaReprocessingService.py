import logging

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from infrastructure.database.connection import (
    connection_pool,
)
from infrastructure.storage import (
    seaweedClient,
)
from repositories.adverseMedia import (
    mediaRepository,
)
from services.adverseMediaPipeline.mediaAcquisitionService import (
    MediaAcquisitionResult,
)
from services.adverseMediaPipeline.mediaRawRecordService import (
    MediaRawRecordService,
)


logger = logging.getLogger(__name__)


class MediaReprocessingService:
    """Load current Raw Media files without contacting the source website."""

    def load(
        self,
        source_config: dict[str, Any],
    ) -> MediaAcquisitionResult:
        source_name = source_config[
            "source_name"
        ]
        dataset_name = source_config[
            "dataset_name"
        ]

        (
            source_id,
            dataset_id,
            raw_files,
        ) = self._find_raw_files(
            source_name=source_name,
            dataset_name=dataset_name,
        )

        records: list[dict[str, Any]] = []
        failed_count = 0
        raw_record_service = MediaRawRecordService()

        with TemporaryDirectory(
            prefix="media-reprocess-"
        ) as temporary_directory:
            temporary_path = Path(
                temporary_directory
            )

            for raw_file in raw_files:
                media_file_id = raw_file["id"]
                record_key = raw_file["record_key"]

                try:
                    local_path = self._download_raw_file(
                        raw_file=raw_file,
                        destination_directory=(
                            temporary_path
                        ),
                    )

                    extracted_records = (
                        raw_record_service.extract(
                            source_config=source_config,
                            source_file_path=local_path,
                            source_url=raw_file.get(
                                "file_url"
                            ),
                        )
                    )

                    if len(extracted_records) != 1:
                        raise RuntimeError(
                            "Expected exactly one Raw Media "
                            "record during REPROCESS."
                        )

                    records.append(
                        {
                            "record_key": record_key,
                            "is_known": True,
                            "extracted": (
                                extracted_records[0]
                            ),
                            "detail_file_path": None,
                            "media_file_id": media_file_id,
                            "file_hash": raw_file.get(
                                "file_hash"
                            ),
                            "storage_path": raw_file.get(
                                "storage_path"
                            ),
                            "raw_status": raw_file.get(
                                "status"
                            ),
                            "is_duplicate": True,
                            "failed": False,
                        }
                    )

                except Exception as error:
                    failed_count += 1

                    logger.exception(
                        "Media Raw reprocessing load failed. "
                        "record_key=%s media_file_id=%s",
                        record_key,
                        media_file_id,
                    )

                    records.append(
                        {
                            "record_key": record_key,
                            "media_file_id": media_file_id,
                            "failed": True,
                            "error": str(error),
                            "error_stage": "REPROCESS_LOAD",
                            "error_type": type(error).__name__,
                        }
                    )

        return MediaAcquisitionResult(
            source_id=source_id,
            dataset_id=dataset_id,
            discovered_count=len(raw_files),
            stored_count=0,
            duplicate_count=0,
            failed_count=failed_count,
            acquired_count=len(raw_files),
            known_count=len(raw_files),
            reached_source_end=True,
            stop_reason="REPROCESS_INPUT_EXHAUSTED",
            completed_safely=(
                failed_count == 0
            ),
            records=records,
        )

    @staticmethod
    def _find_raw_files(
        source_name: str,
        dataset_name: str,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        connection = connection_pool.getconn()

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
                            f"{dataset_name} for source "
                            f"{source_name}"
                        )

                    raw_files = (
                        mediaRepository
                        .find_current_media_files_for_reprocessing(
                            cursor=cursor,
                            source_id=source_id,
                            dataset_id=dataset_id,
                        )
                    )

                    return (
                        source_id,
                        dataset_id,
                        raw_files,
                    )

        finally:
            connection_pool.putconn(
                connection
            )

    @staticmethod
    def _download_raw_file(
        raw_file: dict[str, Any],
        destination_directory: Path,
    ) -> str:
        storage_path = raw_file.get(
            "storage_path"
        )

        if not storage_path:
            raise ValueError(
                "storage_path is missing for "
                f"media_file_id={raw_file['id']}"
            )

        suffix = Path(
            raw_file.get("file_name") or ""
        ).suffix

        if not suffix and raw_file.get("file_type"):
            suffix = (
                "."
                + str(raw_file["file_type"])
                .strip()
                .lstrip(".")
            )

        destination_path = (
            destination_directory
            / f"{raw_file['id']}{suffix}"
        )

        return seaweedClient.download_file(
            storage_path=storage_path,
            destination_path=destination_path,
        )
