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
        parser_version: str | None = None,
    ) -> dict[str, Any]:

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
            "parser_version": parser_version,
            "status": "ACTIVE",
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
        parser_version: str | None = None,
    ) -> dict[str, Any]:
        """
        Process one Media record in one transaction.

        Same record_key + same content_hash:
            skip

        Same record_key + different content_hash:
            insert new version

        New record_key:
            insert
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
                parser_version=parser_version,
            )
        )

        connection = (
            connection_pool.getconn()
        )

        try:
            with connection:
                with connection.cursor() as cursor:

                    exists = (
                        mediaRepository
                        .exists_core_record(
                            cursor=cursor,
                            source_id=source_id,
                            dataset_id=dataset_id,
                            record_key=record_key,
                            content_hash=(
                                core_record[
                                    "content_hash"
                                ]
                            ),
                        )
                    )

                    if exists:
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
                            "inserted": False,
                            "duplicate": True,
                            "core_record_id": None,
                            "record_key": record_key,
                            "content_hash": (
                                core_record[
                                    "content_hash"
                                ]
                            ),
                        }

                    core_record_id = (
                        mediaRepository
                        .insert_media_record(
                            cursor=cursor,
                            record_data=core_record,
                        )
                    )

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
                        "inserted": True,
                        "duplicate": False,
                        "core_record_id": (
                            core_record_id
                        ),
                        "record_key": record_key,
                        "content_hash": (
                            core_record[
                                "content_hash"
                            ]
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