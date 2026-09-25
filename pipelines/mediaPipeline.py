import argparse
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
from services.adverseMediaPipeline.mediaReprocessingService import (
    MediaReprocessingService,
)
from services.common.pipelineVersionService import (
    PipelineVersionService,
)


logger = logging.getLogger(__name__)


MEDIA_CONFIG_PATH = (
    ROOT_DIR
    / "config"
    / "mediaSources.yaml"
)


MEDIA_RUN_MODES = {
    "INITIAL",
    "INCREMENTAL",
    "REPROCESS",
}

def normalize_media_run_mode(
    mode: str,
) -> str:
    """Validate and normalize the requested Media run mode."""

    normalized_mode = str(mode).strip().upper()

    if normalized_mode not in MEDIA_RUN_MODES:
        available_modes = ", ".join(
            sorted(MEDIA_RUN_MODES)
        )

        raise ValueError(
            f"Unknown Media run mode: {mode}. "
            f"Available modes: {available_modes}"
        )

    return normalized_mode


def determine_media_run_status(
    *,
    acquisition_completed_safely: bool,
    total_failed_count: int,
    successful_work_count: int,
) -> str:
    """Return one unambiguous status for schedulers and operators."""

    if (
        acquisition_completed_safely
        and total_failed_count == 0
    ):
        return "SUCCESS"

    if successful_work_count > 0:
        return "PARTIAL"

    return "FAILED"


def media_pipeline_exit_code(
    result: dict[str, Any],
) -> int:
    """Return a non-zero code for partial or failed runs."""

    return (
        0
        if result.get("run_status") == "SUCCESS"
        else 2
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
    mode: str,
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
    run_mode = normalize_media_run_mode(mode)

    # =====================================================
    # 1. Load configuration
    # =====================================================

    media_config = load_media_config()

    pipeline_version = (
    PipelineVersionService.resolve(
        repository_root=ROOT_DIR
    )
)

    logger.info(
        "Media Pipeline version: %s",
        pipeline_version,
    )

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
        "Starting Media Pipeline. "
        "dataset=%s mode=%s",
        dataset_name,
        run_mode,
    )

    if run_mode == "REPROCESS":
        acquisition_result = (
            MediaReprocessingService().load(
                source_config=source_config,
            )
        )

    else:
        acquisition_service = (
            MediaAcquisitionService()
        )

        acquisition_result = (
            acquisition_service.acquire(
                source_config=source_config,
                mode=run_mode,
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
        "dataset=%s mode=%s discovered=%s stored=%s "
        "duplicates=%s failed=%s",
        dataset_name,
        run_mode,
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
    processing_failed_count = 0

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
                        "error_stage": (
                            acquired_record.get(
                                "error_stage",
                                "ACQUISITION",
                            )
                        ),
                        "error_type": (
                            acquired_record.get(
                                "error_type",
                                "AcquisitionError",
                            )
                        ),
                    }
                )

                continue

            # -------------------------------------------------
            # Validate acquisition result
            # -------------------------------------------------

            if media_file_id is None:
                raise ValueError(
                    "media_file_id is missing."
                )

            # -------------------------------------------------
            # Raw Record + PreProcessing
            # -------------------------------------------------

            raw_records = (
                raw_record_service
                .process_acquired_record(
                    source_config=(
                        source_config
                    ),
                    acquired_record=(
                        acquired_record
                    ),
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
                    pipeline_version=pipeline_version,
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

            processing_failed_count += 1

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
                    "error_stage": "PROCESSING",
                    "error_type": type(error).__name__,
                }
            )

    # =====================================================
    # 6. Pipeline result
    # =====================================================

    acquisition_failed_count = (
        acquisition_result.failed_count
    )

    total_failed_count = (
        acquisition_failed_count
        + processing_failed_count
    )

    acquisition_completed_safely = bool(
        getattr(
            acquisition_result,
            "completed_safely",
            True,
        )
    )

    successful_work_count = (
        processed_count
        + int(
            getattr(
                acquisition_result,
                "known_count",
                0,
            )
        )
        + acquisition_result.stored_count
        + acquisition_result.duplicate_count
    )

    run_status = determine_media_run_status(
        acquisition_completed_safely=(
            acquisition_completed_safely
        ),
        total_failed_count=total_failed_count,
        successful_work_count=successful_work_count,
    )

    failure_summary: dict[str, int] = {}

    discovery_failed_count = int(
        getattr(
            acquisition_result,
            "discovery_failed_count",
            0,
        )
    )

    identity_failure_count = int(
        getattr(
            acquisition_result,
            "identity_failure_count",
            0,
        )
    )

    missing_detail_count = int(
        getattr(
            acquisition_result,
            "missing_detail_count",
            0,
        )
    )

    if identity_failure_count:
        failure_summary[
            "DISCOVERY_IDENTITY_FAILED"
        ] = identity_failure_count

    other_discovery_failures = max(
        discovery_failed_count
        - identity_failure_count,
        0,
    )

    if other_discovery_failures:
        failure_summary[
            "SOURCE_DISCOVERY_FAILED"
        ] = other_discovery_failures

    if missing_detail_count:
        failure_summary[
            "DETAIL_RESULT_MISSING"
        ] = missing_detail_count

    for record_result in record_results:
        status = str(
            record_result.get(
                "status",
                "",
            )
        )

        if status.endswith("FAILED"):
            failure_summary[status] = (
                failure_summary.get(status, 0)
                + 1
            )

    result = {
        "run_status": run_status,
        "mode": run_mode,

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

        "acquired_count": int(
            getattr(
                acquisition_result,
                "acquired_count",
                len(acquisition_result.records),
            )
        ),

        "known_count": int(
            getattr(
                acquisition_result,
                "known_count",
                0,
            )
        ),

        "new_count": int(
            getattr(
                acquisition_result,
                "new_count",
                0,
            )
        ),

        "selected_detail_count": int(
            getattr(
                acquisition_result,
                "selected_detail_count",
                0,
            )
        ),

        "missing_detail_count": (
            missing_detail_count
        ),

        "discovery_failed_count": (
            discovery_failed_count
        ),

        "identity_failure_count": (
            identity_failure_count
        ),

        "reached_source_end": getattr(
            acquisition_result,
            "reached_source_end",
            None,
        ),

        "stop_reason": getattr(
            acquisition_result,
            "stop_reason",
            "UNKNOWN",
        ),

        "acquisition_completed_safely": (
            acquisition_completed_safely
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
            acquisition_failed_count
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
            processing_failed_count
        ),

        "total_failed_count": (
            total_failed_count
        ),

        "failure_summary": (
            failure_summary
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
        "dataset=%s mode=%s "
        "status=%s stop_reason=%s "
        "processed=%s "
        "inserted=%s "
        "skipped=%s "
        "failed=%s",
        dataset_name,
        run_mode,
        run_status,
        result["stop_reason"],
        processed_count,
        inserted_count,
        skipped_count,
        total_failed_count,
    )

    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one Adverse Media dataset.",
    )

    parser.add_argument(
        "--dataset",
        required=True,
        help="Dataset key from config/mediaSources.yaml.",
    )

    parser.add_argument(
        "--mode",
        required=True,
        type=str.upper,
        choices=sorted(MEDIA_RUN_MODES),
        help="INITIAL, INCREMENTAL, or REPROCESS.",
    )

    return parser.parse_args()


if __name__ == "__main__":

    configure_logging()
    arguments = _parse_args()

    try:

        pipeline_result = (
            run_media_pipeline(
                dataset_name=arguments.dataset,
                mode=arguments.mode,
            )
        )

        pprint(
            pipeline_result
        )

        exit_code = media_pipeline_exit_code(
            pipeline_result
        )

        if exit_code:
            sys.exit(exit_code)

    except Exception:

        logger.exception(
            "Media Pipeline failed."
        )

        raise
