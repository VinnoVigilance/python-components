from copy import deepcopy
from typing import Any

from infrastructure.database.connection import (
    connection_pool,
)

from repositories.adverseMedia import (
    mediaRepository,
)

from utils.hashing import (
    calculate_record_hash,
)


class MediaCoreService:

    def prepare_core_record(
        self,
        media_payload: dict[str, Any],
        record_key: str,
        external_id: str | None,
        media_file_id: int,
        source_id: int,
        dataset_id: int,
        record_type: str,
        pipeline_version: str,
    ) -> dict[str, Any]:
        """
        Prepare the common fields of one Media version.
        """

        payload = deepcopy(
            media_payload
        )

        content_hash = (
            calculate_record_hash(
                payload
            )
        )

        return {
            "media_file_id": media_file_id,
            "source_id": source_id,
            "dataset_id": dataset_id,
            "external_id": external_id,
            "record_key": record_key,
            "record_type": record_type,
            "media_payload": payload,
            "content_hash": content_hash,
            "pipeline_version": pipeline_version,
        }

    def process(
        self,
        media_payload: dict[str, Any],
        record_key: str,
        external_id: str | None,
        media_file_id: int,
        source_id: int,
        dataset_id: int,
        record_type: str,
        pipeline_version: str,
    ) -> dict[str, Any]:
        """
        Process one Media record in one transaction.

        New logical record:
            Create version 1 with change_type NEW.

        Same content and same pipeline:
            Skip.

        Same content but different pipeline:
            Create a REPROCESSED version.

        Different content:
            Close the previous version and create
            an UPDATED version.
        """

        core_record = (
            self.prepare_core_record(
                media_payload=media_payload,
                record_key=record_key,
                external_id=external_id,
                media_file_id=media_file_id,
                source_id=source_id,
                dataset_id=dataset_id,
                record_type=record_type,
                pipeline_version=pipeline_version,
            )
        )

        connection = (
            connection_pool.getconn()
        )

        try:
            with connection:
                with connection.cursor() as cursor:

                    current_record = (
                        mediaRepository
                        .find_current_media_record(
                            cursor=cursor,
                            dataset_id=dataset_id,
                            record_key=record_key,
                        )
                    )

                    # -----------------------------------------
                    # No change
                    # -----------------------------------------

                    if (
                        current_record is not None
                        and current_record[
                            "content_hash"
                        ]
                        == core_record[
                            "content_hash"
                        ]
                        and current_record[
                            "pipeline_version"
                        ]
                        == pipeline_version
                    ):
                        (
                            mediaRepository
                            .mark_media_file_parsed(
                                cursor=cursor,
                                media_file_id=(
                                    media_file_id
                                ),
                            )
                        )

                        return {
                            "action": "SKIPPED",
                            "inserted": False,
                            "duplicate": True,
                            "core_record_id": (
                                current_record["id"]
                            ),
                            "vv_media_id": (
                                current_record[
                                    "vv_media_id"
                                ]
                            ),
                            "version_no": (
                                current_record[
                                    "version_no"
                                ]
                            ),
                            "record_key": record_key,
                            "content_hash": (
                                core_record[
                                    "content_hash"
                                ]
                            ),
                            "pipeline_version": (
                                pipeline_version
                            ),
                        }

                    # -----------------------------------------
                    # New logical Media record
                    # -----------------------------------------

                    if current_record is None:

                        vv_media_id = (
                            mediaRepository
                            .get_next_vv_media_id(
                                cursor=cursor
                            )
                        )

                        version_no = 1
                        change_type = "NEW"

                    # -----------------------------------------
                    # New version of an existing Media record
                    # -----------------------------------------

                    else:
                        (
                            mediaRepository
                            .close_current_media_record(
                                cursor=cursor,
                                core_record_id=(
                                    current_record["id"]
                                ),
                            )
                        )

                        vv_media_id = (
                            current_record[
                                "vv_media_id"
                            ]
                        )

                        version_no = (
                            current_record[
                                "version_no"
                            ]
                            + 1
                        )

                        if (
                            current_record[
                                "content_hash"
                            ]
                            == core_record[
                                "content_hash"
                            ]
                        ):
                            change_type = (
                                "REPROCESSED"
                            )

                        else:
                            change_type = (
                                "UPDATED"
                            )

                    # -----------------------------------------
                    # Insert new current version
                    # -----------------------------------------

                    record_to_insert = {
                        **core_record,
                        "vv_media_id": (
                            vv_media_id
                        ),
                        "version_no": (
                            version_no
                        ),
                        "change_type": (
                            change_type
                        ),
                    }

                    inserted_record = (
                        mediaRepository
                        .insert_media_record(
                            cursor=cursor,
                            record_data=(
                                record_to_insert
                            ),
                        )
                    )

                    # -----------------------------------------
                    # Mark Raw file as successfully parsed
                    # -----------------------------------------

                    (
                        mediaRepository
                        .mark_media_file_parsed(
                            cursor=cursor,
                            media_file_id=(
                                media_file_id
                            ),
                        )
                    )

                    return {
                        "action": change_type,
                        "inserted": True,
                        "duplicate": False,
                        "core_record_id": (
                            inserted_record["id"]
                        ),
                        "vv_media_id": (
                            inserted_record[
                                "vv_media_id"
                            ]
                        ),
                        "version_no": (
                            inserted_record[
                                "version_no"
                            ]
                        ),
                        "record_key": record_key,
                        "content_hash": (
                            core_record[
                                "content_hash"
                            ]
                        ),
                        "pipeline_version": (
                            pipeline_version
                        ),
                    }

        finally:
            connection_pool.putconn(
                connection
            )

    def mark_failed(
        self,
        media_file_id: int,
    ) -> None:
        """
        Mark one Raw Media file as FAILED.

        This runs in a separate transaction after
        Core processing has been rolled back.
        """

        connection = (
            connection_pool.getconn()
        )

        try:
            with connection:
                with connection.cursor() as cursor:

                    (
                        mediaRepository
                        .mark_media_file_failed(
                            cursor=cursor,
                            media_file_id=(
                                media_file_id
                            ),
                        )
                    )

        finally:
            connection_pool.putconn(
                connection
            )