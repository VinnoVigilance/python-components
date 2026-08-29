# pipelines/watchlistPipeline.py

import logging
import sys
from pathlib import Path
from pprint import pprint
from time import perf_counter
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from config.loggingConfig import configure_logging
from ingestion.downloader import interface as downloader
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import (
    watchlistAttachmentService,
    watchlistCoreService,
    watchlistFileService,
    watchlistRawService,
)


logger = logging.getLogger(__name__)


def run_watchlist_pipeline(watchlist_name: str) -> dict[str, Any]:
    """Run the watchlist ingestion pipeline."""

    if watchlist_name not in WATCHLIST_CONFIGS:
        available_watchlists = ", ".join(sorted(WATCHLIST_CONFIGS))
        raise ValueError(
            f"Unknown watchlist: {watchlist_name}. "
            f"Available watchlists: {available_watchlists}"
        )

    started_at = perf_counter()
    config: dict[str, Any] = WATCHLIST_CONFIGS[watchlist_name]

    acquisition = watchlistFileService.acquire_source(
        config=config,
        downloader=downloader,
    )

    source_file_path = acquisition.source_file_path

    file_metadata = watchlistFileService.calculate_file_metadata(
        file_path=source_file_path
    )

    lookup_values = watchlistFileService.resolve_lookup_values(
        config=config
    )

    duplicate_result = watchlistFileService.check_duplicate(
        source_id=lookup_values["source_id"],
        list_type_id=lookup_values["list_type_id"],
        file_hash=file_metadata["file_hash"],
    )

    duplicate_status = duplicate_result["duplicate_status"]

    result = {
        "watchlist_name": watchlist_name,
        "source_name": config["source_name"],
        "list_name": config["list_name"],
        "source_file_path": str(source_file_path),
        "file_metadata": file_metadata,
        "lookup_values": lookup_values,
        "download_method": config["download_method"],
        "duplicate_status": duplicate_status,
    }

    if duplicate_status == "DUPLICATE_COMPLETED":
        watchlist_file_id = duplicate_result["watchlist_file_id"]

        watchlistFileService.insert_file_log(
            file_id=watchlist_file_id,
            step="DOWNLOAD",
            status="SKIPPED",
            message=(
                "Exact duplicate detected. "
                "The existing file is already normalized."
            ),
        )

        result.update(
            {
                "pipeline_result": "SKIPPED",
                "file_version": duplicate_result["file_version"],
                "storage_path": duplicate_result["storage_path"],
                "watchlist_file_id": watchlist_file_id,
                "parsed_record_count": 0,
                "processed_record_count": 0,
                "raw_record_count": 0,
                "core_processed_count": 0,
                "core_new_count": 0,
                "core_updated_count": 0,
                "core_skipped_count": 0,
                "core_deleted_count": 0,
                "elapsed_seconds": round(
                    perf_counter() - started_at,
                    2,
                ),
            }
        )

        return result

    should_run_raw_processing = True

    if duplicate_status == "RESUME_NORMALIZATION":
        file_version = duplicate_result["file_version"]
        storage_path = duplicate_result["storage_path"]
        watchlist_file_id = duplicate_result["watchlist_file_id"]
        should_run_raw_processing = False

    elif duplicate_status == "RESUME_PROCESSING":
        file_version = duplicate_result["file_version"]
        storage_path = duplicate_result["storage_path"]
        watchlist_file_id = duplicate_result["watchlist_file_id"]

    elif duplicate_status in {
        "FIRST_DOWNLOAD",
        "NEW_VERSION",
    }:
        file_version = watchlistFileService.determine_file_version(
            config=config,
            duplicate_status=duplicate_status,
            source_id=lookup_values["source_id"],
            list_type_id=lookup_values["list_type_id"],
        )

        storage_path = watchlistFileService.store_source_file(
            config=config,
            file_path=source_file_path,
        )

        watchlist_file_id = watchlistFileService.insert_watchlist_file(
            config=config,
            file_metadata=file_metadata,
            source_id=lookup_values["source_id"],
            list_type_id=lookup_values["list_type_id"],
            storage_path=storage_path,
            file_version=file_version,
        )

    else:
        raise RuntimeError(
            f"Unknown duplicate status: {duplicate_status}"
        )

    raw_result = None

    if should_run_raw_processing:
        raw_result = watchlistRawService.process_watchlist_source(
            acquisition=acquisition,
            config=config,
            watchlist_file_id=watchlist_file_id,
        )

    attachment_result = watchlistAttachmentService.process_attachments(
        source_file_path=source_file_path,
        watchlist_file_id=watchlist_file_id,
        config=config,
    )

    core_result = watchlistCoreService.process_watchlist_file(
        watchlist_file_id=watchlist_file_id,
        source_id=lookup_values["source_id"],
        list_type_id=lookup_values["list_type_id"],
        config=config,
    )

    result.update(
        {
            "pipeline_result": "NORMALIZED",
            "file_version": file_version,
            "storage_path": storage_path,
            "watchlist_file_id": watchlist_file_id,
            "attachment_processed_count": attachment_result[
                "processed_count"
            ],
            "attachment_new_count": attachment_result[
                "new_count"
            ],
            "attachment_reused_count": attachment_result[
                "reused_count"
            ],
            "member_attachment_count": attachment_result[
                "member_mapping_count"
            ],
            "list_attachment_count": attachment_result[
                "list_mapping_count"
            ],
            "core_processed_count": core_result[
                "processed_count"
            ],
            "core_new_count": core_result[
                "new_count"
            ],
            "core_updated_count": core_result[
                "updated_count"
            ],
            "core_skipped_count": core_result[
                "skipped_count"
            ],
            "core_deleted_count": core_result[
                "deleted_count"
            ],
            "elapsed_seconds": round(
                perf_counter() - started_at,
                2,
            ),
        }
    )

    if raw_result is None:
        result.update(
            {
                "parsed_record_count": 0,
                "processed_record_count": 0,
                "raw_record_count": 0,
            }
        )

    else:
        result.update(
            {
                "parsed_record_count": raw_result[
                    "parsed_record_count"
                ],
                "processed_record_count": raw_result[
                    "processed_record_count"
                ],
                "raw_record_count": raw_result[
                    "raw_record_count"
                ],
            }
        )

    return result


if __name__ == "__main__":
    configure_logging()

    try:
        pipeline_result = run_watchlist_pipeline(
            watchlist_name="PH-HOUSE-MEMBERS"
        )

        pprint(pipeline_result)

    except Exception:
        logger.exception(
            "Watchlist pipeline execution failed."
        )

        raise