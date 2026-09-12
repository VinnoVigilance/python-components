from typing import Any

from psycopg2.extras import Json


# =========================================================
# Lookup
# =========================================================


def find_media_source_id(
    cursor,
    source_name: str,
) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM common.lkup_media_source
        WHERE name = %s
        LIMIT 1
        """,
        (source_name,),
    )

    row = cursor.fetchone()

    return row[0] if row else None


def find_media_dataset_id(
    cursor,
    dataset_name: str,
    source_id: int,
) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM common.lkup_media_dataset
        WHERE name = %s
          AND source_id = %s
        LIMIT 1
        """,
        (
            dataset_name,
            source_id,
        ),
    )

    row = cursor.fetchone()

    return row[0] if row else None


# =========================================================
# Core Discovery
# =========================================================


def exists_by_record_key(
    cursor,
    source_id: int,
    dataset_id: int,
    record_key: str,
) -> bool:
    """
    Used during Discovery.

    At this point content_hash does not exist yet.
    """

    cursor.execute(
        """
        SELECT 1
        FROM core.media_record
        WHERE source_id = %s
          AND dataset_id = %s
          AND record_key = %s
        LIMIT 1
        """,
        (
            source_id,
            dataset_id,
            record_key,
        ),
    )

    return cursor.fetchone() is not None


# =========================================================
# Core Media Record
# =========================================================


def exists_core_record(
    cursor,
    source_id: int,
    dataset_id: int,
    record_key: str,
    content_hash: str,
) -> bool:
    """
    Check whether this exact version already exists.
    """

    cursor.execute(
        """
        SELECT 1
        FROM core.media_record
        WHERE source_id = %s
          AND dataset_id = %s
          AND record_key = %s
          AND content_hash = %s
        LIMIT 1
        """,
        (
            source_id,
            dataset_id,
            record_key,
            content_hash,
        ),
    )

    return cursor.fetchone() is not None


def insert_media_record(
    cursor,
    record_data: dict[str, Any],
) -> int:
    """
    Insert one standardized Media record into Core.
    """

    query_data = {
        **record_data,
        "media_payload": Json(
            record_data["media_payload"]
        ),
    }

    cursor.execute(
        """
        INSERT INTO core.media_record (
            media_file_id,
            source_id,
            dataset_id,
            external_id,
            record_key,
            record_type,
            media_payload,
            content_hash,
            parser_version,
            parsed_at,
            status
        )
        VALUES (
            %(media_file_id)s,
            %(source_id)s,
            %(dataset_id)s,
            %(external_id)s,
            %(record_key)s,
            %(record_type)s,
            %(media_payload)s,
            %(content_hash)s,
            %(parser_version)s,
            NOW(),
            %(status)s
        )
        RETURNING id
        """,
        query_data,
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            "Failed to insert core.media_record."
        )

    return row[0]


# =========================================================
# Raw Media File
# =========================================================


def find_media_file_by_hash(
    cursor,
    file_hash: str,
) -> dict[str, Any] | None:
    """
    Find an already acquired physical file.
    """

    cursor.execute(
        """
        SELECT
            id,
            source_id,
            dataset_id,
            file_hash,
            storage_path,
            status,
            parsed_at
        FROM raw.media_file
        WHERE file_hash = %s
        LIMIT 1
        """,
        (file_hash,),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "source_id": row[1],
        "dataset_id": row[2],
        "file_hash": row[3],
        "storage_path": row[4],
        "status": row[5],
        "parsed_at": row[6],
    }


def insert_media_file(
    cursor,
    file_data: dict[str, Any],
) -> int | None:
    """
    Insert one newly acquired Raw Media file.
    """

    cursor.execute(
        """
        INSERT INTO raw.media_file (
            source_id,
            dataset_id,
            file_url,
            file_name,
            file_type,
            mime_type,
            storage_path,
            file_size,
            file_hash,
            file_version,
            downloaded_at,
            parsed_at,
            status,
            download_method
        )
        VALUES (
            %(source_id)s,
            %(dataset_id)s,
            %(file_url)s,
            %(file_name)s,
            %(file_type)s,
            %(mime_type)s,
            %(storage_path)s,
            %(file_size)s,
            %(file_hash)s,
            1,
            NOW(),
            NULL,
            'DOWNLOADED',
            %(download_method)s
        )
        ON CONFLICT (file_hash)
        DO NOTHING
        RETURNING id
        """,
        file_data,
    )

    row = cursor.fetchone()

    return row[0] if row else None


def mark_media_file_parsed(
    cursor,
    media_file_id: int,
) -> None:
    """
    Mark successfully processed Raw Media file as PARSED.
    """

    cursor.execute(
        """
        UPDATE raw.media_file
        SET
            status = 'PARSED',
            parsed_at = NOW()
        WHERE id = %s
        """,
        (media_file_id,),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            f"raw.media_file not found: {media_file_id}"
        )


def mark_media_file_failed(
    cursor,
    media_file_id: int,
) -> None:
    """
    Mark unsuccessfully processed Raw Media file as FAILED.
    """

    cursor.execute(
        """
        UPDATE raw.media_file
        SET
            status = 'FAILED',
            parsed_at = NULL
        WHERE id = %s
        """,
        (media_file_id,),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            f"raw.media_file not found: {media_file_id}"
        )