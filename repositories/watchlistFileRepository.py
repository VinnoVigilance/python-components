def find_source_id(cursor,source_name: str,) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM common.lkup_source
        WHERE name = %s
        """,
        (source_name,),
    )

    row = cursor.fetchone()

    return row[0] if row else None


def find_list_type_id(cursor,source_id: int,list_name: str,) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM common.lkup_source_list_type
        WHERE source_id = %s
          AND name = %s
        """,
        (
            source_id,
            list_name,
        ),
    )

    row = cursor.fetchone()

    return row[0] if row else None

def find_latest_file(
    cursor,
    source_id: int,
    list_type_id: int,
) -> dict | None:
    cursor.execute(
        """
        SELECT
            wf.id,
            wf.file_hash,
            wf.file_version,
            wf.storage_path,
            wf.status,

            EXISTS (
                SELECT 1
                FROM raw.unparsed_watchlist_payload payload
                WHERE payload.watchlist_file_id = wf.id
            ) AS has_raw_payloads,

            EXISTS (
                SELECT 1
                FROM raw.watchlist_file_log file_log
                WHERE file_log.file_id = wf.id
                  AND file_log.step = 'NORMALIZATION'
                  AND file_log.status = 'SUCCESS'
            ) AS normalization_completed

        FROM raw.watchlist_file wf
        WHERE wf.source_id = %s
          AND wf.list_type_id = %s
        ORDER BY
            wf.downloaded_at DESC,
            wf.id DESC
        LIMIT 1
        """,
        (
            source_id,
            list_type_id,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "file_hash": row[1],
        "file_version": row[2],
        "storage_path": row[3],
        "status": row[4],
        "has_raw_payloads": row[5],
        "normalization_completed": row[6],
    }

def find_latest_file_version(cursor,source_id: int,list_type_id: int,) -> str | None:
    cursor.execute(
        """
        SELECT file_version
        FROM raw.watchlist_file
        WHERE source_id = %s
          AND list_type_id = %s
        ORDER BY
            downloaded_at DESC,
            id DESC
        LIMIT 1
        """,
        (
            source_id,
            list_type_id,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return row[0]

def insert_watchlist_file(
    cursor,
    file_data: dict,
) -> int:
    cursor.execute(
        """
        INSERT INTO raw.watchlist_file (
            source_id,
            list_type_id,
            list_url,
            storage_path,
            file_name,
            file_type,
            mime_type,
            file_size,
            file_hash,
            file_version,
            download_method
        )
        VALUES (
            %(source_id)s,
            %(list_type_id)s,
            %(list_url)s,
            %(storage_path)s,
            %(file_name)s,
            %(file_type)s,
            %(mime_type)s,
            %(file_size)s,
            %(file_hash)s,
            %(file_version)s,
            %(download_method)s
        )
        RETURNING id
        """,
        file_data,
    )

    row = cursor.fetchone()

    return row[0]

def mark_file_as_parsed(
    cursor,
    watchlist_file_id: int,
) -> None:
    cursor.execute(
        """
        UPDATE raw.watchlist_file
        SET
            status = 'PARSED',
            parsed_at = NOW()
        WHERE id = %s
        """,
        (watchlist_file_id,),
    )


def mark_file_as_failed(
    cursor,
    watchlist_file_id: int,
) -> None:
    cursor.execute(
        """
        UPDATE raw.watchlist_file
        SET status = 'FAILED'
        WHERE id = %s
        """,
        (watchlist_file_id,),
    )

    