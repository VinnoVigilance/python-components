"""SQL access for watchlist risk assessments and their source versions."""

from psycopg2.extras import Json, RealDictCursor


def find_scan_end(cursor, mode, effective_date=None):
    if mode == "incremental":
        cursor.execute("SELECT COALESCE(MAX(id), 0) FROM delivery.watchlist_daily_delta_actions WHERE effective_date = %s", (effective_date,))
    else:
        cursor.execute("SELECT COALESCE(MAX(id), 0) FROM core.watchlist_member WHERE is_current = TRUE")
    return cursor.fetchone()[0]


def find_current_batch(connection, after_id, end_id, batch_size, source_name=None):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT id, vv_member_id AS subject_id, version_no, full_payload AS payload, change_type
            FROM core.watchlist_member
            WHERE is_current = TRUE AND id > %(after)s AND id <= %(end)s
              AND (%(source)s IS NULL OR full_payload @> jsonb_build_object('Sources', jsonb_build_array(jsonb_build_object('ListName', %(source)s))))
            ORDER BY id LIMIT %(size)s
        """, {"after": after_id, "end": end_id, "size": batch_size, "source": source_name})
        return [dict(row) for row in cursor.fetchall()]


def find_delta_batch(connection, effective_date, after_id, end_id, batch_size):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT id, action, vv_member_id AS subject_id, watchlist_member_id AS source_record_id
            FROM delivery.watchlist_daily_delta_actions
            WHERE effective_date = %s AND id > %s AND id <= %s
            ORDER BY id LIMIT %s
        """, (effective_date, after_id, end_id, batch_size))
        return [dict(row) for row in cursor.fetchall()]


def find_current_source(connection, subject_id):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT id, vv_member_id AS subject_id, version_no, full_payload AS payload, change_type
            FROM core.watchlist_member WHERE vv_member_id = %s AND is_current = TRUE
            ORDER BY id DESC LIMIT 1 FOR UPDATE
        """, (subject_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def find_current_risk(cursor, subject_id):
    cursor.execute("""
        SELECT risk_details_hash, version_no FROM core.member_risk_category
        WHERE vv_member_id = %s AND is_current = TRUE
        ORDER BY id DESC LIMIT 1 FOR UPDATE
    """, (subject_id,))
    row = cursor.fetchone()
    return {"risk_details_hash": row[0], "version_no": row[1]} if row else None


def expire_current(cursor, subject_id):
    cursor.execute("""
        UPDATE core.member_risk_category SET is_current = FALSE, valid_to = NOW()
        WHERE vv_member_id = %s AND is_current = TRUE
    """, (subject_id,))
    return cursor.rowcount


def insert_risk(cursor, record, details, result_hash):
    cursor.execute("""
        INSERT INTO core.member_risk_category (
            vv_member_id, watchlist_member_id, version_no, risk_details, risk_details_hash,
            valid_from, valid_to, is_current
        ) VALUES (%s, %s, %s, %s, %s, NOW(), NULL, TRUE) RETURNING id
    """, (record["subject_id"], record["id"], record["version_no"], Json(details), result_hash))
    return cursor.fetchone()[0]
