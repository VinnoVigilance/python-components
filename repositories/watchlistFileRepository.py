def find_source_id(
    cursor,
    source_name: str,
) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM common.lkup_source_list
        WHERE name = %s
        """,
        (source_name,),
    )

    row = cursor.fetchone()

    return row[0] if row else None


def find_list_type_id(
    cursor,
    source_id: int,
    list_name: str,
) -> int | None:
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
):
    cursor.execute(
        """
        SELECT
            id,
            file_hash
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

    return {
        "id": row[0],
        "file_hash": row[1],
    }