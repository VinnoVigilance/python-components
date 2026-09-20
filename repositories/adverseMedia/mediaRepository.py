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


def find_current_media_record(
    cursor,
    dataset_id: int,
    record_key: str,
) -> dict[str, Any] | None:
    """
    Find and lock the current version of one Media record.
    """

    cursor.execute(
        """
        SELECT
            id,
            media_file_id,
            vv_media_id,
            source_id,
            dataset_id,
            external_id,
            record_key,
            record_type,
            content_hash,
            version_no,
            pipeline_version,
            change_type
        FROM core.media_record
        WHERE dataset_id = %s
          AND record_key = %s
          AND is_current = TRUE
        LIMIT 1
        FOR UPDATE
        """,
        (
            dataset_id,
            record_key,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "media_file_id": row[1],
        "vv_media_id": row[2],
        "source_id": row[3],
        "dataset_id": row[4],
        "external_id": row[5],
        "record_key": row[6],
        "record_type": row[7],
        "content_hash": row[8],
        "version_no": row[9],
        "pipeline_version": row[10],
        "change_type": row[11],
    }


def get_next_vv_media_id(
    cursor,
) -> int:
    """
    Allocate a new permanent Media identifier.
    """

    cursor.execute(
        """
        SELECT nextval(
            'core.vv_media_id_seq'
        )
        """
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            "Could not generate vv_media_id."
        )

    return row[0]


def close_current_media_record(
    cursor,
    core_record_id: int,
) -> None:
    """
    Close the previous current version.
    """

    cursor.execute(
        """
        UPDATE core.media_record
        SET
            is_current = FALSE,
            valid_to = NOW()
        WHERE id = %s
          AND is_current = TRUE
        """,
        (core_record_id,),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            "Current Media record could not be closed. "
            f"Core record ID: {core_record_id}"
        )


def insert_media_record(
    cursor,
    record_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Insert one version of a Media record.
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
            vv_media_id,
            source_id,
            dataset_id,
            external_id,
            record_key,
            record_type,
            media_payload,
            content_hash,
            version_no,
            is_current,
            valid_from,
            valid_to,
            change_type,
            pipeline_version
        )
        VALUES (
            %(media_file_id)s,
            %(vv_media_id)s,
            %(source_id)s,
            %(dataset_id)s,
            %(external_id)s,
            %(record_key)s,
            %(record_type)s,
            %(media_payload)s,
            %(content_hash)s,
            %(version_no)s,
            TRUE,
            NOW(),
            NULL,
            %(change_type)s,
            %(pipeline_version)s
        )
        RETURNING
            id,
            vv_media_id,
            version_no
        """,
        query_data,
    )

    row = cursor.fetchone()

    if row is None:
        raise RuntimeError(
            "Failed to insert core.media_record."
        )

    return {
        "id": row[0],
        "vv_media_id": row[1],
        "version_no": row[2],
    }

# =========================================================
# Raw Media File
# =========================================================


def find_media_file_by_hash(
    cursor,
    source_id: int,
    dataset_id: int,
    file_hash: str,
) -> dict[str, Any] | None:
    """
    Find an already acquired physical Media file.

    A file is considered duplicate only inside the same
    source and dataset.
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
        WHERE source_id = %s
          AND dataset_id = %s
          AND file_hash = %s
        LIMIT 1
        """,
        (
            source_id,
            dataset_id,
            file_hash,
        ),
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

    Duplicate detection is scoped to:
        source_id + dataset_id + file_hash
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
        ON CONFLICT (
            source_id,
            dataset_id,
            file_hash
        )
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