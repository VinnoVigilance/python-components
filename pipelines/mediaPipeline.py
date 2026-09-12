import logging
import sys
from pathlib import Path
from pprint import pprint
from time import perf_counter
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from config.loggingConfig import configure_logging

from services.adverseMediaPipeline.mediaAcquisitionService import (
    MediaAcquisitionService,
)
from services.adverseMediaPipeline.mediaRawRecordService import (
    MediaRawRecordService,
)
from services.adverseMediaPipeline.mediaNormalizationService import (
    MediaNormalizationService,
)
from services.adverseMediaPipeline.mediaCoreService import (
    MediaCoreService,
)
from services.adverseMediaPipeline.mediaIdentityService import (
    MediaIdentityService,
)


logger = logging.getLogger(__name__)


MEDIA_CONFIG_PATH = (
    ROOT_DIR
    / "config"
    / "mediaSources.yaml"
)


def load_media_config() -> dict[str, Any]:
    """
    Load Media configuration.
    """

    with MEDIA_CONFIG_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError(
            "Invalid mediaSources.yaml configuration."
        )

    return config


def run_media_pipeline(
    dataset_name: str,
) -> dict[str, Any]:
    """
    Run Media Pipeline.

    Flow:

        Acquisition
        -> Raw Record
        -> PreProcessing
        -> Normalization
        -> Core

    Each Media record is processed independently.
    A failure in one record must not rollback
    previously processed records.
    """

    started_at = perf_counter()

    # =====================================================
    # 1. Load configuration
    # =====================================================

    media_config = load_media_config()

    global_config = media_config.get(
        "global",
        {},
    )

    sources = media_config.get(
        "sources",
        {},
    )

    if dataset_name not in sources:

        available_sources = ", ".join(
            sorted(
                sources.keys()
            )
        )

        raise ValueError(
            f"Unknown Media dataset: {dataset_name}. "
            f"Available datasets: {available_sources}"
        )

    source_config = sources[
        dataset_name
    ]

    if not source_config.get(
        "enabled",
        True,
    ):
        raise ValueError(
            f"Media dataset is disabled: "
            f"{dataset_name}"
        )

    # =====================================================
    # 2. Acquisition
    # =====================================================

    logger.info(
        "Starting Media acquisition. "
        "dataset=%s",
        dataset_name,
    )

    acquisition_service = (
        MediaAcquisitionService()
    )

    acquisition_result = (
        acquisition_service.acquire(
            source_config=source_config
        )
    )

    source_id = (
        acquisition_result.source_id
    )

    dataset_id = (
        acquisition_result.dataset_id
    )

    logger.info(
        "Media acquisition completed. "
        "dataset=%s discovered=%s stored=%s "
        "duplicates=%s failed=%s",
        dataset_name,
        acquisition_result.discovered_count,
        acquisition_result.stored_count,
        acquisition_result.duplicate_count,
        acquisition_result.failed_count,
    )

    # =====================================================
    # 3. Create processing services
    # =====================================================

    raw_record_service = (
        MediaRawRecordService()
    )

    normalization_service = (
        MediaNormalizationService(
            global_config=global_config,
            source_config=source_config,
        )
    )

    core_service = (
        MediaCoreService()
    )

    # =====================================================
    # 4. Processing counters
    # =====================================================

    processed_count = 0
    inserted_count = 0
    skipped_count = 0
    failed_count = 0

    record_results: list[
        dict[str, Any]
    ] = []

    # =====================================================
    # 5. Process every Media record independently
    # =====================================================

    for acquired_record in (
        acquisition_result.records
    ):

        record_key = (
            acquired_record.get(
                "record_key"
            )
        )

        media_file_id = (
            acquired_record.get(
                "media_file_id"
            )
        )

        try:

            # -------------------------------------------------
            # Acquisition failure
            # -------------------------------------------------

            if acquired_record.get(
                "failed"
            ):

                failed_count += 1

                record_results.append(
                    {
                        "record_key": (
                            record_key
                        ),
                        "media_file_id": (
                            media_file_id
                        ),
                        "status": (
                            "ACQUISITION_FAILED"
                        ),
                        "error": (
                            acquired_record.get(
                                "error"
                            )
                        ),
                    }
                )

                continue

            # -------------------------------------------------
            # Extracted Raw Record
            # -------------------------------------------------

            extracted = (
                acquired_record.get(
                    "extracted"
                )
            )

            if not extracted:
                raise ValueError(
                    "Acquired Media record "
                    "has no extracted data."
                )

            if media_file_id is None:
                raise ValueError(
                    "media_file_id is missing."
                )

            # -------------------------------------------------
            # Raw Record + PreProcessing
            # -------------------------------------------------

            raw_records = (
                raw_record_service.process(
                    source_config=(
                        source_config
                    ),
                    records=[
                        extracted
                    ],
                )
            )

            if len(raw_records) != 1:
                raise RuntimeError(
                    "Expected exactly one "
                    "Raw Media record."
                )

            raw_record = (
                raw_records[0]
            )

            # -------------------------------------------------
            # Normalization
            # -------------------------------------------------

            media_payload = (
                normalization_service
                .normalize(
                    raw_record
                )
            )

            # -------------------------------------------------
            # Identity
            # -------------------------------------------------

            if not record_key:

                record_key = (
                    MediaIdentityService
                    .generate_record_key(
                        source_config=(
                            source_config
                        ),
                        record=(
                            raw_record
                        ),
                    )
                )

            external_id = (
                MediaIdentityService
                .extract_external_id(
                    source_config=(
                        source_config
                    ),
                    record=(
                        raw_record
                    ),
                )
            )

            # -------------------------------------------------
            # Record type
            # -------------------------------------------------

            record_type = (
                source_config.get(
                    "dataset_category"
                )
            )

            if not record_type:
                raise ValueError(
                    "dataset_category "
                    "is required."
                )

            # -------------------------------------------------
            # Core
            # -------------------------------------------------

            core_result = (
                core_service.process(
                    media_payload=(
                        media_payload
                    ),
                    record_key=(
                        record_key
                    ),
                    external_id=(
                        external_id
                    ),
                    media_file_id=(
                        media_file_id
                    ),
                    source_id=(
                        source_id
                    ),
                    dataset_id=(
                        dataset_id
                    ),
                    record_type=(
                        record_type
                    ),
                )
            )

            processed_count += 1

            if core_result[
                "inserted"
            ]:

                inserted_count += 1
                status = "INSERTED"

            else:

                skipped_count += 1
                status = "SKIPPED"

            record_results.append(
                {
                    "record_key": (
                        record_key
                    ),
                    "media_file_id": (
                        media_file_id
                    ),
                    "status": (
                        status
                    ),
                    "content_hash": (
                        core_result[
                            "content_hash"
                        ]
                    ),
                    "core_record_id": (
                        core_result[
                            "core_record_id"
                        ]
                    ),
                }
            )

        except Exception as error:

            failed_count += 1

            logger.exception(
                "Media processing failed. "
                "record_key=%s "
                "media_file_id=%s",
                record_key,
                media_file_id,
            )

            # -------------------------------------------------
            # Mark Raw file as FAILED
            # -------------------------------------------------

            if media_file_id is not None:

                try:

                    core_service.mark_failed(
                        media_file_id=(
                            media_file_id
                        )
                    )

                except Exception:

                    logger.exception(
                        "Failed to mark Media file "
                        "as FAILED. "
                        "media_file_id=%s",
                        media_file_id,
                    )

            record_results.append(
                {
                    "record_key": (
                        record_key
                    ),
                    "media_file_id": (
                        media_file_id
                    ),
                    "status": (
                        "PROCESSING_FAILED"
                    ),
                    "error": (
                        str(error)
                    ),
                }
            )

    # =====================================================
    # 6. Pipeline result
    # =====================================================

    result = {
        "dataset_name": (
            dataset_name
        ),

        "source_name": (
            source_config[
                "source_name"
            ]
        ),

        "source_id": (
            source_id
        ),

        "dataset_id": (
            dataset_id
        ),

        "discovered_count": (
            acquisition_result
            .discovered_count
        ),

        "stored_count": (
            acquisition_result
            .stored_count
        ),

        "acquisition_duplicate_count": (
            acquisition_result
            .duplicate_count
        ),

        "acquisition_failed_count": (
            acquisition_result
            .failed_count
        ),

        "processed_count": (
            processed_count
        ),

        "core_inserted_count": (
            inserted_count
        ),

        "core_skipped_count": (
            skipped_count
        ),

        "processing_failed_count": (
            failed_count
        ),

        "elapsed_seconds": round(
            perf_counter()
            - started_at,
            2,
        ),

        "records": (
            record_results
        ),
    }

    logger.info(
        "Media Pipeline completed. "
        "dataset=%s "
        "processed=%s "
        "inserted=%s "
        "skipped=%s "
        "failed=%s",
        dataset_name,
        processed_count,
        inserted_count,
        skipped_count,
        failed_count,
    )

    return result


if __name__ == "__main__":

    configure_logging()

    try:

        pipeline_result = (
            run_media_pipeline(
                dataset_name=(
                    "NBI_PRESS_RELEASES"
                )
            )
        )

        pprint(
            pipeline_result
        )

    except Exception:

        logger.exception(
            "Media Pipeline failed."
        )

        raise