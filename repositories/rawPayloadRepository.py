from psycopg2.extras import Json, execute_values


def insert_raw_payloads(
    cursor,
    watchlist_file_id: int,
    payloads: list[tuple[str, dict]],
) -> int:
    values = [
        (
            watchlist_file_id,
            external_id,
            Json(raw_json),
        )
        for external_id, raw_json in payloads
    ]

    execute_values(
        cursor,
        """
        INSERT INTO raw.unparsed_watchlist_payload (
            watchlist_file_id,
            external_id,
            raw_json
        )
        VALUES %s
        """,
        values,
        page_size=1000,
    )

    return len(values)

def find_raw_payload_batch(
    cursor,
    watchlist_file_id: int,
    last_raw_member_id: int = 0,
    batch_size: int = 1000,
) -> list[dict]:
    cursor.execute(
        """
        SELECT
            id,
            external_id,
            raw_json
        FROM raw.unparsed_watchlist_payload
        WHERE watchlist_file_id = %s
          AND id > %s
        ORDER BY id
        LIMIT %s
        """,
        (
            watchlist_file_id,
            last_raw_member_id,
            batch_size,
        ),
    )

    rows = cursor.fetchall()

    return [
        {
            "id": row[0],
            "external_id": row[1],
            "raw_json": row[2],
        }
        for row in rows
    ]