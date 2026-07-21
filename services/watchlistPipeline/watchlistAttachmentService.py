import logging
from pathlib import Path
from time import perf_counter
from typing import Any

from infrastructure.database.connection import connection_pool
from infrastructure.storage import seaweedClient
from repositories import (
    attachmentRepository,
    rawPayloadRepository,
    watchlistFileLogRepository,
)
from services.watchlistPipeline import watchlistFileService


logger = logging.getLogger(__name__)

BATCH_SIZE = 1000


def process_attachments(
    source_file_path: Path,
    watchlist_file_id: int,
    config: dict[str, Any],
) -> dict[str, int]:
    """Store attachments and register their Raw relationships."""

    attachment_rules = config.get("attachments", [])

    result = {
        "processed_count": 0,
        "new_count": 0,
        "reused_count": 0,
        "member_mapping_count": 0,
        "list_mapping_count": 0,
    }

    if not attachment_rules:
        return result

    if not isinstance(attachment_rules, list):
        raise TypeError(
            "Watchlist 'attachments' config must be a list."
        )

    started_at = perf_counter()
    source_file_path = Path(source_file_path).resolve()
    day_directory = source_file_path.parent

    year_part = day_directory.parent.parent.name
    month_part = day_directory.parent.name
    day_part = day_directory.name

    attachment_jobs: list[dict[str, Any]] = []

    watchlistFileService.insert_file_log(
        file_id=watchlist_file_id,
        step="ATTACHMENT",
        status="STARTED",
        message="Attachment processing started.",
    )

    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                member_rules = [
                    rule
                    for rule in attachment_rules
                    if rule.get("scope") == "member"
                ]

                if member_rules:
                    last_raw_member_id = 0

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
                            external_id = str(
                                raw_record["external_id"]
                            ).strip()

                            raw_json = raw_record["raw_json"]

                            for rule in member_rules:
                                local_path_value: Any = raw_json

                                for field_name in rule[
                                    "local_path_field"
                                ].split("."):
                                    if not isinstance(
                                        local_path_value,
                                        dict,
                                    ):
                                        local_path_value = None
                                        break

                                    local_path_value = (
                                        local_path_value.get(
                                            field_name
                                        )
                                    )

                                source_url_value: Any = raw_json
                                source_url_field = rule.get(
                                    "source_url_field"
                                )

                                if source_url_field:
                                    for field_name in (
                                        source_url_field.split(".")
                                    ):
                                        if not isinstance(
                                            source_url_value,
                                            dict,
                                        ):
                                            source_url_value = None
                                            break

                                        source_url_value = (
                                            source_url_value.get(
                                                field_name
                                            )
                                        )
                                else:
                                    source_url_value = None

                                if (
                                    local_path_value is None
                                    or local_path_value == ""
                                ):
                                    continue

                                local_paths = (
                                    local_path_value
                                    if isinstance(
                                        local_path_value,
                                        list,
                                    )
                                    else [local_path_value]
                                )

                                source_urls = (
                                    source_url_value
                                    if isinstance(
                                        source_url_value,
                                        list,
                                    )
                                    else (
                                        [source_url_value]
                                        if source_url_value
                                        else []
                                    )
                                )

                                for index, local_path in enumerate(
                                    local_paths
                                ):
                                    source_url = None

                                    if source_urls:
                                        source_url = str(
                                            source_urls[index]
                                            if index < len(
                                                source_urls
                                            )
                                            else source_urls[0]
                                        ).strip() or None

                                    attachment_jobs.append(
                                        {
                                            "scope": "member",
                                            "file_path": Path(
                                                str(local_path)
                                            ).resolve(),
                                            "source_url": source_url,
                                            "external_id": external_id,
                                            "attachment_type": rule[
                                                "attachment_type"
                                            ],
                                        }
                                    )

                        last_raw_member_id = raw_records[-1][
                            "id"
                        ]

                list_rules = [
                    rule
                    for rule in attachment_rules
                    if rule.get("scope") == "list"
                ]

                for rule in list_rules:
                    attachment_directory = (
                        day_directory
                        / rule["local_directory"]
                    )

                    if not attachment_directory.is_dir():
                        continue

                    for file_path in sorted(
                        attachment_directory.iterdir()
                    ):
                        if file_path.is_file():
                            attachment_jobs.append(
                                {
                                    "scope": "list",
                                    "file_path": file_path,
                                    "source_url": rule.get(
                                        "source_url"
                                    ),
                                    "external_id": None,
                                    "attachment_type": None,
                                }
                            )

                for job in attachment_jobs:
                    file_path = job["file_path"]

                    if not file_path.is_file():
                        raise FileNotFoundError(
                            f"Attachment file not found: {file_path}"
                        )

                    metadata = (
                        watchlistFileService
                        .calculate_file_metadata(
                            file_path=file_path,
                        )
                    )

                    existing_attachment = (
                        attachmentRepository
                        .find_attachment_by_hash(
                            cursor=cursor,
                            file_hash=metadata["file_hash"],
                        )
                    )

                    if existing_attachment is None:
                        if job["scope"] == "member":
                            safe_external_id = (
                                job["external_id"]
                                .replace("/", "_")
                                .replace("\\", "_")
                            )

                            attachment_path = (
                                f"members/{safe_external_id}"
                            )
                        else:
                            attachment_path = "list"

                        storage_file_name = (
                            f"{metadata['file_hash']}_"
                            f"{metadata['file_name']}"
                        )

                        object_path = (
                            f"{config['source_name']}/"
                            f"{config['list_name']}/"
                            f"{year_part}/"
                            f"{month_part}/"
                            f"{day_part}/"
                            f"attachments/"
                            f"{attachment_path}/"
                            f"{storage_file_name}"
                        )

                        storage_path = seaweedClient.upload_file(
                            file_path=file_path,
                            object_path=object_path,
                        )

                        attachment_id = (
                            attachmentRepository
                            .insert_attachment(
                                cursor=cursor,
                                attachment_data={
                                    "storage_path": storage_path,
                                    "file_name": metadata[
                                        "file_name"
                                    ],
                                    "file_type": metadata[
                                        "file_type"
                                    ],
                                    "mime_type": metadata[
                                        "mime_type"
                                    ],
                                    "file_size": metadata[
                                        "file_size"
                                    ],
                                    "file_hash": metadata[
                                        "file_hash"
                                    ],
                                    "source_url": job[
                                        "source_url"
                                    ],
                                },
                            )
                        )

                        result["new_count"] += 1
                    else:
                        attachment_id = existing_attachment[
                            "id"
                        ]
                        result["reused_count"] += 1

                    if job["scope"] == "member":
                        existing_mapping = (
                            attachmentRepository
                            .find_member_attachment(
                                cursor=cursor,
                                external_id=job["external_id"],
                                attachment_id=attachment_id,
                                attachment_type=job[
                                    "attachment_type"
                                ],
                            )
                        )

                        if existing_mapping is None:
                            attachmentRepository.insert_member_attachment(
                                cursor=cursor,
                                external_id=job["external_id"],
                                attachment_id=attachment_id,
                                attachment_type=job[
                                    "attachment_type"
                                ],
                            )

                            result[
                                "member_mapping_count"
                            ] += 1

                    else:
                        existing_mapping = (
                            attachmentRepository
                            .find_list_attachment(
                                cursor=cursor,
                                raw_file_id=watchlist_file_id,
                                attachment_id=attachment_id,
                            )
                        )

                        if existing_mapping is None:
                            attachmentRepository.insert_list_attachment(
                                cursor=cursor,
                                raw_file_id=watchlist_file_id,
                                attachment_id=attachment_id,
                            )

                            result[
                                "list_mapping_count"
                            ] += 1

                    result["processed_count"] += 1

                duration_ms = int(
                    (perf_counter() - started_at) * 1000
                )

                watchlistFileLogRepository.insert_file_log(
                    cursor=cursor,
                    file_id=watchlist_file_id,
                    step="ATTACHMENT",
                    status="SUCCESS",
                    message=(
                        "Attachment processing completed. "
                        f"Processed: {result['processed_count']}, "
                        f"New: {result['new_count']}, "
                        f"Reused: {result['reused_count']}."
                    ),
                    duration_ms=duration_ms,
                )

        return result

    except Exception as error:
        duration_ms = int(
            (perf_counter() - started_at) * 1000
        )

        try:
            watchlistFileService.mark_watchlist_file_as_failed(
                watchlist_file_id=watchlist_file_id,
                step="ATTACHMENT",
                error=error,
                duration_ms=duration_ms,
            )
        except Exception:
            logger.exception(
                "Failed to register attachment failure."
            )

        logger.exception(
            "Attachment processing failed. file_id=%s",
            watchlist_file_id,
        )

        raise

    finally:
        connection_pool.putconn(connection)
