"""SQL access for media risk assessments and their source versions."""

from psycopg2.extras import Json, RealDictCursor


def latest_risk_date(cursor):
    cursor.execute("SELECT MAX(created_at) FROM core.media_risk_category")
    value = cursor.fetchone()[0]
    return value.astimezone().date() if value else None


def find_scan_end(cursor, mode, effective_date=None):
    if mode == "incremental":
        cursor.execute("SELECT COALESCE(MAX(id), 0) FROM delivery.media_daily_delta_actions WHERE effective_date = %s", (effective_date,))
    else:
        cursor.execute("SELECT COALESCE(MAX(id), 0) FROM core.media_record WHERE is_current = TRUE")
    return cursor.fetchone()[0]


def find_current_batch(connection, after_id, end_id, batch_size, source_name=None):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT id, vv_media_id AS subject_id, version_no, media_payload AS payload, change_type
            FROM core.media_record
            WHERE is_current = TRUE AND id > %(after)s AND id <= %(end)s
              AND (%(source)s IS NULL OR dataset_id IN (SELECT id FROM common.lkup_media_dataset WHERE name = %(source)s))
            ORDER BY id LIMIT %(size)s
        """, {"after": after_id, "end": end_id, "size": batch_size, "source": source_name})
        return [dict(row) for row in cursor.fetchall()]


def find_delta_batch(connection, effective_date, after_id, end_id, batch_size):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT id, action, vv_media_id AS subject_id, media_record_id AS source_record_id
            FROM delivery.media_daily_delta_actions
            WHERE effective_date = %s AND id > %s AND id <= %s
            ORDER BY id LIMIT %s
        """, (effective_date, after_id, end_id, batch_size))
        return [dict(row) for row in cursor.fetchall()]


def find_current_source(connection, subject_id):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT id, vv_media_id AS subject_id, version_no, media_payload AS payload, change_type
            FROM core.media_record WHERE vv_media_id = %s AND is_current = TRUE
            ORDER BY id DESC LIMIT 1 FOR UPDATE
        """, (subject_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def find_source_version(connection, source_record_id):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT id, vv_media_id AS subject_id, version_no, media_payload AS payload, change_type
            FROM core.media_record WHERE id = %s
        """, (source_record_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def find_current_risk(cursor, subject_id):
    cursor.execute("""
        SELECT risk_details_hash, version_no, media_record_id FROM core.media_risk_category
        WHERE vv_media_id = %s AND is_current = TRUE
        ORDER BY id DESC LIMIT 1 FOR UPDATE
    """, (subject_id,))
    row = cursor.fetchone()
    return {"risk_details_hash": row[0], "version_no": row[1], "source_record_id": row[2]} if row else None


def has_assessment(cursor, subject_id, source_record_id, result_hash):
    cursor.execute("""
        SELECT 1 FROM core.media_risk_category
        WHERE vv_media_id = %s AND media_record_id = %s AND risk_details_hash = %s
        LIMIT 1
    """, (subject_id, source_record_id, result_hash))
    return cursor.fetchone() is not None


def expire_current(cursor, subject_id):
    cursor.execute("""
        UPDATE core.media_risk_category SET is_current = FALSE, valid_to = NOW()
        WHERE vv_media_id = %s AND is_current = TRUE
    """, (subject_id,))
    return cursor.rowcount


def insert_risk(cursor, record, details, result_hash, current=True):
    cursor.execute("""
        INSERT INTO core.media_risk_category (
            vv_media_id, media_record_id, version_no, risk_details, risk_details_hash,
            valid_from, valid_to, is_current
        ) VALUES (%s, %s, %s, %s, %s, NOW(), CASE WHEN %s THEN NULL ELSE NOW() END, %s)
        RETURNING id
    """, (record["subject_id"], record["id"], record["version_no"], Json(details),
          result_hash, current, current))
    return cursor.fetchone()[0]
