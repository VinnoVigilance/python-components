def insert_file_log(
    cursor,
    file_id: int,
    step: str,
    status: str,
    message: str | None = None,
    error_code: str | None = None,
    error_details: str | None = None,
    duration_ms: int | None = None,
) -> int:
    cursor.execute(
        """
        INSERT INTO raw.watchlist_file_log (
            file_id,
            step,
            status,
            message,
            error_code,
            error_details,
            duration_ms
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING id
        """,
        (
            file_id,
            step,
            status,
            message,
            error_code,
            error_details,
            duration_ms,
        ),
    )

    row = cursor.fetchone()

    return row[0]