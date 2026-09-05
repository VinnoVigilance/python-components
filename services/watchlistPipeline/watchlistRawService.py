import logging
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any

from infrastructure.database.connection import connection_pool
from parsing.parserFactory import create_parser
from repositories import (
    rawPayloadRepository,
    watchlistFileLogRepository,
    watchlistFileRepository,
)
from services.watchlistPipeline import watchlistFileService
from transforms.preProcessingEngine import PreProcessingEngine
from ingestion.crawler.interface import crawl
from ingestion.crawler.models import CrawlerTask


logger = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parents[2]


def process_watchlist_source(
    acquisition,
    config: dict[str, Any],
    watchlist_file_id: int,
) -> dict[str, int]:
    """Extract, preprocess and store a watchlist source in the Raw Layer."""

    file_path = acquisition.source_file_path

    extraction_method = str(
    config.get(
        "extraction_method",
        "",
    )
).strip().upper()

    if acquisition.records is not None:
        parsed_records = acquisition.records

    elif extraction_method == "SAVED_HTML_SPIDER":
        source_config_path = config.get(
            "source_config"
        )

        if not source_config_path:
            raise ValueError(
                "SAVED_HTML_SPIDER source must define "
                "'source_config'."
            )

        crawl_result = crawl(
            CrawlerTask(
                url=config["url"],
                source_name=config["source_name"],
                list_name=config.get(
                    "list_name",
                    config["source_name"],
                ),
                source_config_path=str(
                    (
                        ROOT_DIR
                        / source_config_path
                    ).resolve()
                ),
                source_file_path=str(
                    file_path
                ),
                download_dir=str(
                    ROOT_DIR
                    / "data"
                    / "downloads"
                ),
            )
        )

        parsed_records = list(
            crawl_result.records
        )

    else:
        parser = create_parser(
            file_type=config["file_type"]
        )

        parsed_records = list(
            parser.parse(
                file_path=file_path,
                config=config,
            )
        )

    minimum_record_count = config.get(
        "minimum_record_count"
    )

    if (
        minimum_record_count is not None
        and len(parsed_records) < int(minimum_record_count)
    ):
        raise ValueError(
            f"Only {len(parsed_records)} records were extracted; "
            f"minimum expected count is {minimum_record_count}."
        )

    return process_records(
        records=parsed_records,
        file_path=file_path,
        config=config,
        watchlist_file_id=watchlist_file_id,
    )


def process_records(
    records: list[dict[str, Any]],
    file_path: Path,
    config: dict[str, Any],
    watchlist_file_id: int,
) -> dict[str, int]:
    """Preprocess and store already extracted records in the Raw Layer."""

    current_step = "PARSING"
    step_started_at = perf_counter()

    watchlistFileService.insert_file_log(
        file_id=watchlist_file_id,
        step="PARSING",
        status="STARTED",
        message="Record processing started.",
    )

    try:
        preprocessing_rules = deepcopy(config.get("preprocessing", []))

        for rule in preprocessing_rules:
            rule_config = rule.get("config", {})

            for path_field in rule.get("relative_path_fields", []):
                relative_path = rule_config.get(path_field)

                if relative_path:
                    rule_config[path_field] = str(
                        file_path.parent / relative_path
                    )

        processed_records = PreProcessingEngine().preprocess(
            records=records,
            rules=preprocessing_rules,
        )

        if not processed_records:
            raise ValueError(
                "Extraction and preprocessing returned no records."
            )

        processing_duration_ms = int(
            (perf_counter() - step_started_at) * 1000
        )

        watchlistFileService.insert_file_log(
            file_id=watchlist_file_id,
            step="PARSING",
            status="SUCCESS",
            message=(
                f"{len(processed_records)} records "
                "extracted and preprocessed successfully."
            ),
            duration_ms=processing_duration_ms,
        )

        current_step = "RAW_INSERT"
        step_started_at = perf_counter()

        raw_record_count = insert_raw_payloads(
            watchlist_file_id=watchlist_file_id,
            records=processed_records,
            external_id_path=config["external_id_path"],
        )

        return {
            "parsed_record_count": len(records),
            "processed_record_count": len(processed_records),
            "raw_record_count": raw_record_count,
        }

    except Exception as error:
        duration_ms = int(
            (perf_counter() - step_started_at) * 1000
        )

        try:
            watchlistFileService.mark_watchlist_file_as_failed(
                watchlist_file_id=watchlist_file_id,
                step=current_step,
                error=error,
                duration_ms=duration_ms,
            )

        except Exception:
            logger.exception(
                "Failed to update file status and insert database failure log."
            )

        logger.exception(
            "Raw processing failed. file_id=%s",
            watchlist_file_id,
        )

        raise


def insert_raw_payloads(
    watchlist_file_id: int,
    records: list[dict[str, Any]],
    external_id_path: str,
) -> int:
    """Insert all processed records into the Raw Layer."""

    payloads = []

    for record_index, record in enumerate(records, start=1):
        external_id = str(
            record.get(external_id_path, "")
        ).strip()

        if not external_id:
            raise ValueError(
                f"External ID was not found for record number {record_index}. "
                f"Expected field: {external_id_path}"
            )

        payloads.append(
            (
                external_id,
                record,
            )
        )

    started_at = perf_counter()
    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                inserted_count = (
                    rawPayloadRepository.insert_raw_payloads(
                        cursor=cursor,
                        watchlist_file_id=watchlist_file_id,
                        payloads=payloads,
                    )
                )

                watchlistFileRepository.mark_file_as_parsed(
                    cursor=cursor,
                    watchlist_file_id=watchlist_file_id,
                )

                duration_ms = int(
                    (perf_counter() - started_at) * 1000
                )

                watchlistFileLogRepository.insert_file_log(
                    cursor=cursor,
                    file_id=watchlist_file_id,
                    step="RAW_INSERT",
                    status="SUCCESS",
                    message=f"{inserted_count} raw records inserted successfully.",
                    duration_ms=duration_ms,
                )

        return inserted_count

    finally:
        connection_pool.putconn(connection)