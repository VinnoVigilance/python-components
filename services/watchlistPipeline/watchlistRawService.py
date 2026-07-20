import logging
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


logger = logging.getLogger(__name__)


def process_watchlist_file(
    file_path: Path,
    config: dict[str, Any],
    watchlist_file_id: int,
) -> dict[str, int]:
    """Parse, preprocess and store a source file in the Raw Layer."""

    current_step = "PARSING"
    step_started_at = perf_counter()

    watchlistFileService.insert_file_log(
        file_id=watchlist_file_id,
        step="PARSING",
        status="STARTED",
        message="File parsing started.",
    )

    try:
        parser = create_parser(
            file_type=config["file_type"],
        )

        parsed_records = list(
            parser.parse(
                file_path=file_path,
                config=config,
            )
        )

        preprocessing_engine = PreProcessingEngine()

        processed_records = preprocessing_engine.preprocess(
            records=parsed_records,
            rules=config.get("preprocessing", []),
        )

        if not processed_records:
            raise ValueError(
                "Parser and preprocessing returned no records."
            )

        parsing_duration_ms = int(
            (perf_counter() - step_started_at) * 1000
        )

        watchlistFileService.insert_file_log(
            file_id=watchlist_file_id,
            step="PARSING",
            status="SUCCESS",
            message=(
                f"{len(processed_records)} records "
                "parsed and preprocessed successfully."
            ),
            duration_ms=parsing_duration_ms,
        )

        current_step = "RAW_INSERT"
        step_started_at = perf_counter()

        raw_record_count = insert_raw_payloads(
            watchlist_file_id=watchlist_file_id,
            records=processed_records,
            external_id_path=config["external_id_path"],
        )

        return {
            "parsed_record_count": len(parsed_records),
            "processed_record_count": len(
                processed_records
            ),
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
                "Failed to update file status and "
                "insert database failure log."
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

    for record_index, record in enumerate(
        records,
        start=1,
    ):
        external_id = str(
            record.get(external_id_path, "")
        ).strip()

        if not external_id:
            raise ValueError(
                "External ID was not found for "
                f"record number {record_index}. "
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
                    message=(
                        f"{inserted_count} raw records "
                        "inserted successfully."
                    ),
                    duration_ms=duration_ms,
                )

        return inserted_count

    finally:
        connection_pool.putconn(connection)