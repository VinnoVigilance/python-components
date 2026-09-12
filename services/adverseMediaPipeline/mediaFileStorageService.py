from datetime import datetime
from pathlib import Path
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


class MediaRawService:
    """
    Stores acquired Media files in durable Raw storage.

    Flow:
        duplicate check
        -> SeaweedFS upload
        -> raw.media_file insert
    """

    @staticmethod
    def register_file(
        source_id: int,
        dataset_id: int,
        source_name: str,
        dataset_name: str,
        file_metadata: dict[str, Any],
        download_method: str,
    ) -> dict[str, Any]:

        file_hash = file_metadata.get(
            "file_hash"
        )

        if not file_hash:
            raise ValueError(
                "file_hash is required."
            )

        #
        # 1. Check duplicate
        #

        existing_file = (
            MediaRawService._find_existing_file(
                file_hash=file_hash
            )
        )

        if existing_file is not None:
            return {
                "media_file_id": existing_file["id"],
                "is_duplicate": True,
                "status": existing_file["status"],
                "storage_path": (
                    existing_file["storage_path"]
                ),
                "file_hash": file_hash,
            }

        #
        # 2. Upload original file to SeaweedFS
        #

        storage_path = (
            MediaRawService._store_file(
                source_name=source_name,
                dataset_name=dataset_name,
                local_path=file_metadata[
                    "local_path"
                ],
            )
        )

        #
        # 3. Insert Raw DB record
        #

        file_data = {
            "source_id": source_id,
            "dataset_id": dataset_id,

            "file_url": file_metadata.get(
                "file_url"
            ),

            "file_name": file_metadata[
                "file_name"
            ],

            "file_type": file_metadata[
                "file_type"
            ],

            "mime_type": file_metadata[
                "mime_type"
            ],

            "file_size": file_metadata[
                "file_size"
            ],

            "file_hash": file_hash,

            # IMPORTANT:
            # This is now SeaweedFS path,
            # not local disk path.
            "storage_path": storage_path,

            "download_method": (
                download_method
            ),
        }

        media_file_id = (
            MediaRawService._insert_raw_file(
                file_data=file_data
            )
        )

        return {
            "media_file_id": media_file_id,
            "is_duplicate": False,
            "status": "DOWNLOADED",
            "storage_path": storage_path,
            "file_hash": file_hash,
        }

    @staticmethod
    def _find_existing_file(
        file_hash: str,
    ) -> dict[str, Any] | None:

        connection = connection_pool.getconn()

        try:
            with connection:
                with connection.cursor() as cursor:

                    return (
                        mediaRepository
                        .find_media_file_by_hash(
                            cursor=cursor,
                            file_hash=file_hash,
                        )
                    )

        finally:
            connection_pool.putconn(
                connection
            )

    @staticmethod
    def _store_file(
        source_name: str,
        dataset_name: str,
        local_path: str,
    ) -> str:

        path = Path(local_path)

        stored_at = datetime.now()

        object_path = (
            f"{source_name}/"
            f"{dataset_name}/"
            f"year={stored_at:%Y}/"
            f"month={stored_at:%m}/"
            f"day={stored_at:%d}/"
            f"{path.name}"
        )

        return seaweedClient.upload_file(
            file_path=path,
            object_path=object_path,
        )

    @staticmethod
    def _insert_raw_file(
        file_data: dict[str, Any],
    ) -> int:

        connection = connection_pool.getconn()

        try:
            with connection:
                with connection.cursor() as cursor:

                    media_file_id = (
                        mediaRepository
                        .insert_media_file(
                            cursor=cursor,
                            file_data=file_data,
                        )
                    )

                    if media_file_id is None:
                        raise RuntimeError(
                            "raw.media_file insert failed."
                        )

                    return media_file_id

        finally:
            connection_pool.putconn(
                connection
            )