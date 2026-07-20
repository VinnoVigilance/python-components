import logging
from time import perf_counter
from typing import Any

from infrastructure.database.connection import connection_pool
from repositories import (
    coreMemberRepository,
    rawPayloadRepository,
    watchlistFileLogRepository,
)
from services.watchlistPipeline import (
    watchlistFileService,
    watchlistNormalizationService,
)
from utils.hashing import calculate_record_hash


logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


def process_watchlist_file(
    watchlist_file_id: int,
    source_id: int,
    list_type_id: int,
    config: dict[str, Any],
) -> dict[str, int]:
    """Normalize Raw records and synchronize the Core Layer."""

    started_at = perf_counter()

    watchlistFileService.insert_file_log(
        file_id=watchlist_file_id,
        step="NORMALIZATION",
        status="STARTED",
        message="Watchlist Core processing started.",
    )

    try:
        (
            pre_normalizer,
            mapper,
            post_normalizer,
        ) = (
            watchlistNormalizationService
            .create_normalization_engines(
                config=config,
            )
        )

        processed_count = 0
        new_count = 0
        updated_count = 0
        deleted_count = 0
        skipped_count = 0

        last_raw_member_id = 0
        entity_type_cache: dict[str, int] = {}

        connection = connection_pool.getconn()

        try:
            with connection:
                with connection.cursor() as cursor:
                    while True:
                        raw_records = (
                            rawPayloadRepository
                            .find_raw_payload_batch(
                                cursor=cursor,
                                watchlist_file_id=(
                                    watchlist_file_id
                                ),
                                last_raw_member_id=(
                                    last_raw_member_id
                                ),
                                batch_size=BATCH_SIZE,
                            )
                        )

                        if not raw_records:
                            break

                        for raw_record in raw_records:
                            raw_member_id = raw_record["id"]
                            external_id = raw_record[
                                "external_id"
                            ]
                            raw_json = raw_record["raw_json"]

                            canonical_record = (
                                watchlistNormalizationService
                                .normalize_record(
                                    raw_record=raw_json,
                                    config=config,
                                    pre_normalizer=(
                                        pre_normalizer
                                    ),
                                    mapper=mapper,
                                    post_normalizer=(
                                        post_normalizer
                                    ),
                                )
                            )

                            record_hash = (
                                calculate_record_hash(
                                    canonical_record
                                )
                            )

                            current_member = (
                                coreMemberRepository
                                .find_current_member(
                                    cursor=cursor,
                                    source_id=source_id,
                                    list_type_id=list_type_id,
                                    external_id=external_id,
                                )
                            )

                            if (
                                current_member is not None
                                and current_member[
                                    "record_hash"
                                ] == record_hash
                            ):
                                skipped_count += 1
                                processed_count += 1
                                continue

                            entity_type_name = str(
                                canonical_record.get(
                                    "EntityType",
                                    "",
                                )
                            ).strip()

                            if not entity_type_name:
                                raise ValueError(
                                    "Canonical EntityType "
                                    "was not found. "
                                    f"Raw member ID: "
                                    f"{raw_member_id}"
                                )

                            entity_type_id = (
                                entity_type_cache.get(
                                    entity_type_name
                                )
                            )

                            if entity_type_id is None:
                                entity_type_id = (
                                    coreMemberRepository
                                    .find_entity_type_id(
                                        cursor=cursor,
                                        entity_type_name=(
                                            entity_type_name
                                        ),
                                    )
                                )

                                if entity_type_id is None:
                                    raise LookupError(
                                        "Entity type was not "
                                        "found in lookup table: "
                                        f"{entity_type_name}"
                                    )

                                entity_type_cache[
                                    entity_type_name
                                ] = entity_type_id

                            member_data = {
                                "raw_file_id": (
                                    watchlist_file_id
                                ),
                                "raw_member_id": raw_member_id,
                                "source_id": source_id,
                                "list_type_id": list_type_id,
                                "external_id": external_id,
                                "entity_type_id": (
                                    entity_type_id
                                ),
                                "record_hash": record_hash,
                                "full_payload": (
                                    canonical_record
                                ),
                            }

                            if current_member is None:
                                (
                                    coreMemberRepository
                                    .insert_new_member(
                                        cursor=cursor,
                                        member_data=member_data,
                                    )
                                )

                                new_count += 1

                            else:
                                (
                                    coreMemberRepository
                                    .close_current_member(
                                        cursor=cursor,
                                        core_member_id=(
                                            current_member["id"]
                                        ),
                                    )
                                )

                                next_version_no = (
                                    current_member["version_no"]
                                    + 1
                                )

                                (
                                    coreMemberRepository
                                    .insert_updated_member(
                                        cursor=cursor,
                                        member_data=member_data,
                                        vv_member_id=(
                                            current_member[
                                                "vv_member_id"
                                            ]
                                        ),
                                        version_no=(
                                            next_version_no
                                        ),
                                    )
                                )

                                updated_count += 1

                            processed_count += 1

                        last_raw_member_id = raw_records[-1][
                            "id"
                        ]

                    if processed_count == 0:
                        raise ValueError(
                            "No Raw records were found "
                            "for Core processing."
                        )

                    if (
                        config.get("versioning_strategy")
                        == "continuous"
                    ):
                        deleted_members = (
                            coreMemberRepository
                            .find_deleted_current_members(
                                cursor=cursor,
                                source_id=source_id,
                                list_type_id=list_type_id,
                                watchlist_file_id=(
                                    watchlist_file_id
                                ),
                            )
                        )

                        for current_member in deleted_members:
                            (
                                coreMemberRepository
                                .close_current_member(
                                    cursor=cursor,
                                    core_member_id=(
                                        current_member["id"]
                                    ),
                                )
                            )

                            (
                                coreMemberRepository
                                .insert_deleted_member(
                                    cursor=cursor,
                                    current_member=(
                                        current_member
                                    ),
                                    watchlist_file_id=(
                                        watchlist_file_id
                                    ),
                                )
                            )

                            deleted_count += 1

                    duration_ms = int(
                        (perf_counter() - started_at) * 1000
                    )

                    watchlistFileLogRepository.insert_file_log(
                        cursor=cursor,
                        file_id=watchlist_file_id,
                        step="NORMALIZATION",
                        status="SUCCESS",
                        message=(
                            "Core processing completed. "
                            f"New: {new_count}, "
                            f"Updated: {updated_count}, "
                            f"Deleted: {deleted_count}, "
                            f"Skipped: {skipped_count}."
                        ),
                        duration_ms=duration_ms,
                    )

        finally:
            connection_pool.putconn(connection)

        return {
            "processed_count": processed_count,
            "new_count": new_count,
            "updated_count": updated_count,
            "deleted_count": deleted_count,
            "skipped_count": skipped_count,
        }

    except Exception as error:
        duration_ms = int(
            (perf_counter() - started_at) * 1000
        )

        try:
            watchlistFileService.mark_watchlist_file_as_failed(
                watchlist_file_id=watchlist_file_id,
                step="NORMALIZATION",
                error=error,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.exception(
                "Failed to update file status and "
                "insert Core processing failure log."
            )

        logger.exception(
            "Core processing failed. file_id=%s",
            watchlist_file_id,
        )

        raise