"""Read successful source checks from the existing watchlist file history."""


def find_last_successful_check(cursor, source_id: int, list_type_id: int):
    """Return the time a list was last fully processed or checked unchanged.

    PARSED on watchlist_file only means Raw processing completed. A committed
    NORMALIZATION/SUCCESS log means Core processing completed. An unchanged
    download is only eligible when that file has previously completed Core.
    """
    cursor.execute(
        """
        SELECT file_log.event_time
        FROM raw.watchlist_file AS file
        JOIN raw.watchlist_file_log AS file_log
          ON file_log.file_id = file.id
        WHERE file.source_id = %s
          AND file.list_type_id = %s
          AND (
              (file_log.step = 'NORMALIZATION'
               AND file_log.status = 'SUCCESS')
              OR
              (file_log.step = 'DOWNLOAD'
               AND file_log.status = 'SKIPPED'
               AND EXISTS (
                   SELECT 1
                   FROM raw.watchlist_file_log AS core_log
                   WHERE core_log.file_id = file.id
                     AND core_log.step = 'NORMALIZATION'
                     AND core_log.status = 'SUCCESS'
               ))
          )
        ORDER BY file_log.event_time DESC, file_log.id DESC
        LIMIT 1
        """,
        (source_id, list_type_id),
    )
    row = cursor.fetchone()
    return row[0] if row else None