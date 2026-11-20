"""Persistence and bounded selection for Media Member Post Processing.

Only the member destination is writable. No schema migration is performed.
All write decisions are revalidated while the current core row is locked.
"""
from __future__ import annotations

from datetime import date, timedelta
from psycopg2.extras import Json, RealDictCursor


class PendingSource(RuntimeError):
    """Source/analysis is missing, inconsistent, or changed during inference."""


REQUIRED_COLUMNS = frozenset({
    'id', 'media_record_id', 'vv_media_id', 'media_version',
    'extracted_member_id', 'member_payload', 'processor_manifest', 'member_hash',
    'from_date', 'to_date', 'is_current', 'created_at',
})


def preflight(connection):
    """Validate the architect's 12-column writable-flag table; return JSONB-only mode."""
    with connection.cursor() as cur:
        cur.execute('''
            SELECT a.attname, a.attgenerated
            FROM pg_attribute a
            WHERE a.attrelid = to_regclass('core.media_member_post_process')
              AND a.attnum > 0 AND NOT a.attisdropped
        ''')
        columns = dict(cur.fetchall())
        missing = REQUIRED_COLUMNS - columns.keys()
        if missing:
            raise RuntimeError(f'Missing media member columns: {sorted(missing)}')
        if columns['is_current']:
            raise RuntimeError('core.media_member_post_process.is_current is GENERATED; '
                               'the approved 2026-11-18 ALTER must be applied externally')
        # The DB architect's 2026-11-15 DDL intentionally has 12 columns.
        # Names and entity types belong in member_payload JSONB, not separate
        # SQL columns. A generated preflight must not dictate schema changes.
        cur.execute('''SELECT count(*) FROM (
            SELECT vv_media_id FROM core.media_member_post_process
            WHERE is_current = TRUE
            GROUP BY vv_media_id, extracted_member_id HAVING count(*) > 1
        ) violations''')
        if cur.fetchone()[0]:
            raise RuntimeError('Duplicate current local member identifiers: resolve before processing')
        cur.execute('''SELECT count(*) FROM core.media_member_post_process
                       WHERE (is_current = TRUE AND to_date IS NOT NULL)
                          OR (is_current = FALSE AND to_date IS NULL)''')
        if cur.fetchone()[0]:
            raise RuntimeError('Existing member is_current/to_date inconsistencies; investigate before writing')
        # Read-only upstream contract checks. The source article-analysis code
        # may still expire by writing to_date only (legacy GENERATED flag).
        # After the 2026-11-18 ALTER this is unsafe; we never edit that code.
        cur.execute('''SELECT 1 FROM core.media_record_post_process
                       WHERE (is_current = TRUE AND to_date IS NOT NULL)
                          OR (is_current = FALSE AND to_date IS NULL) LIMIT 1''')
        if cur.fetchone():
            raise RuntimeError('Upstream analysis is_current/to_date mismatch. '
                               'The upstream owner must fix their writer; member job does not modify it')
        cur.execute('''SELECT vv_media_id FROM core.media_record_post_process
                       WHERE is_current = TRUE GROUP BY vv_media_id
                       HAVING count(*) > 1 LIMIT 1''')
        conflict = cur.fetchone()
        if conflict:
            raise RuntimeError(f'Upstream has multiple current analyses for {conflict[0]}; '
                               'ask upstream owner to resolve before running members')
        # False selects the documented JSONB-only persistence contract.
        return False


def scan_bounds(connection):
    """Freeze available upper IDs at invocation; late arrivals require replay."""
    with connection.cursor() as cur:
        cur.execute('SELECT COALESCE(MAX(id),0) FROM core.media_record')
        core_id = cur.fetchone()[0]
        cur.execute('SELECT COALESCE(MAX(id),0) FROM delivery.media_daily_delta_actions')
        delta_id = cur.fetchone()[0]
        cur.execute('SELECT COALESCE(MAX(id),0) FROM core.media_record_post_process')
        analysis_id = cur.fetchone()[0]
        return core_id, delta_id, analysis_id


def validate_delta_references(connection, start: date, end: date, delta_cap: int):
    """Reject broken delta references rather than silently losing work."""
    with connection.cursor() as cur:
        cur.execute('''
            SELECT d.id FROM delivery.media_daily_delta_actions d
            LEFT JOIN core.media_record m ON m.id = d.media_record_id
            WHERE d.effective_date BETWEEN %s AND %s AND d.id <= %s
              AND (m.id IS NULL OR m.vv_media_id IS DISTINCT FROM d.vv_media_id
                   OR d.action NOT IN ('ADD','UPDATE','DELETE'))
            ORDER BY d.id LIMIT 1
        ''', (start, end, delta_cap))
        found = cur.fetchone()
        if found:
            raise PendingSource(f'Delta row {found[0]} has invalid action or core reference')


def find_initial_batch(connection, after_id, core_cap, batch_size):
    with connection.cursor() as cur:
        cur.execute('''SELECT id, vv_media_id FROM core.media_record
            WHERE id > %s AND id <= %s AND is_current = TRUE
              AND COALESCE(UPPER(change_type),'') <> 'DELETED'
            ORDER BY id LIMIT %s''', (after_id, core_cap, batch_size))
        return cur.fetchall()


def find_incremental_batch(connection, after_vv, delta_cap, analysis_cap,
                           start: date, end: date, batch_size):
    """Deduplicated candidates from *both* core delta and analysis updates.

    Analysis updates are read via the existing created_at index, without
    assuming the downstream's last inserted timestamp is a progress cursor.
    """
    next_day = end + timedelta(days=1)
    with connection.cursor() as cur:
        cur.execute('''
            SELECT vv_media_id FROM (
                SELECT DISTINCT d.vv_media_id FROM delivery.media_daily_delta_actions d
                  WHERE d.id <= %s AND d.effective_date BETWEEN %s AND %s
                    AND d.vv_media_id > %s
                UNION
                SELECT DISTINCT p.vv_media_id FROM core.media_record_post_process p
                  WHERE p.id <= %s AND p.created_at >= %s AND p.created_at < %s
                    AND p.vv_media_id > %s
            ) changes ORDER BY vv_media_id LIMIT %s
        ''', (delta_cap, start, end, after_vv,
              analysis_cap, start, next_day, after_vv, batch_size))
        return [row[0] for row in cur.fetchall()]


def snapshot(connection, vv_media_id, *, lock=False):
    """Read current article & current analysis. Safe across prior version carry-forward.

    The first pipeline intentionally retains an older current analysis row if
    analysis_payload hash matches, even when the core version increments.
    Consumers must schedule this job AFTER that pipeline has finished.
    """
    lock_core = ' FOR UPDATE' if lock else ''
    lock_analysis = ' FOR SHARE' if lock else ''
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT id, vv_media_id, version_no, media_payload, change_type
                       FROM core.media_record WHERE vv_media_id=%s AND is_current=TRUE'''
                    + lock_core, (vv_media_id,))
        core_rows = cur.fetchall()
        if len(core_rows) > 1:
            raise PendingSource(f'Multiple current core versions for {vv_media_id}')
        if not core_rows:
            # A DELETE delta can legitimately leave no active core row. Only a
            # latest explicit deletion tombstone is sufficient to expire members.
            cur.execute('''SELECT id, vv_media_id, version_no, media_payload, change_type
                           FROM core.media_record WHERE vv_media_id=%s
                           ORDER BY version_no DESC, id DESC LIMIT 1''' + lock_core,
                        (vv_media_id,))
            found = cur.fetchone()
            if found is None or (found['change_type'] or '').upper() != 'DELETED':
                raise PendingSource(f'No current core version or verified delete for {vv_media_id}')
            core = dict(found)
        else:
            core = dict(core_rows[0])
        if (core['change_type'] or '').upper() == 'DELETED':
            return {'vv_media_id':vv_media_id, 'core_id':core['id'],
                    'version':core['version_no'], 'deleted':True}
        cur.execute('''SELECT id, media_record_id, media_version, analysis_payload, post_process_hash
                       FROM core.media_record_post_process
                       WHERE vv_media_id=%s AND is_current=TRUE''' + lock_analysis,
                    (vv_media_id,))
        rows = cur.fetchall()
        if len(rows) != 1:
            raise PendingSource(f'Expected one current adverse analysis for {vv_media_id}, got {len(rows)}')
        analysis = dict(rows[0])
        if analysis['media_version'] > core['version_no']:
            raise PendingSource('Analysis is newer than current core record')
        if analysis['media_version'] == core['version_no'] and analysis['media_record_id'] != core['id']:
            raise PendingSource('Analysis points to a different core row of the same version')
        payload = analysis['analysis_payload']
        prediction = payload.get('adversePrediction') if isinstance(payload, dict) else None
        if not isinstance(prediction, dict) or prediction.get('status') != 'SCORED' or type(prediction.get('modelPositive')) is not bool:
            raise PendingSource(f'No valid scored adverse decision for {vv_media_id}')
        content = core['media_payload'].get('Content') if isinstance(core['media_payload'], dict) else None
        if not isinstance(content, dict):
            content = {}
        title, body = content.get('Title'), content.get('BodyText')
        if title is not None and not isinstance(title, str):
            raise PendingSource('Content.Title is not a string')
        if body is not None and not isinstance(body, str):
            raise PendingSource('Content.BodyText is not a string')
        return {
            'vv_media_id': vv_media_id, 'core_id': core['id'], 'version': core['version_no'],
            'deleted': False, 'analysis_id':analysis['id'],
            'analysis_hash': analysis['post_process_hash'],
            'analysis_source_version':analysis['media_version'],
            'positive':prediction['modelPositive'], 'title': title or '', 'body':body or '',
        }


def current_members(connection, vv_media_id):
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT id, media_record_id, media_version, extracted_member_id,
                              member_hash, processor_manifest
                       FROM core.media_member_post_process
                       WHERE vv_media_id=%s AND is_current=TRUE ORDER BY id''', (vv_media_id,))
        return [dict(row) for row in cur.fetchall()]


def save_members(connection, expected, prepared, manifest, *, extra_columns=False):
    """Transactional full-set replacement, serialized by the current core row.

    All historic local identifiers are read only after the source lock is held.
    Never update or delete history; only expire and insert a new full set.
    """
    current_snapshot = snapshot(connection, expected['vv_media_id'], lock=True)
    if current_snapshot != expected:
        raise PendingSource('Article/analysis changed while entity extraction was running')
    news_id = expected['vv_media_id']
    with connection.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute('''SELECT id, media_record_id, media_version, extracted_member_id, member_hash
            FROM core.media_member_post_process WHERE vv_media_id=%s AND is_current=TRUE
            ORDER BY id FOR UPDATE''', (news_id,))
        old = [dict(row) for row in cur.fetchall()]
        hashes_same = sorted(row['member_hash'] for row in old) == sorted(x['hash'] for x in prepared)
        version_same = all(row['media_record_id'] == expected['core_id'] and
                           row['media_version'] == expected['version'] for row in old)
        if hashes_same and version_same:
            return 'UNCHANGED' if prepared else 'EMPTY_UNCHANGED'
        cur.execute('''SELECT COALESCE(MAX(extracted_member_id),0)
                       FROM core.media_member_post_process WHERE vv_media_id=%s''', (news_id,))
        last_id = cur.fetchone()['coalesce']
        cur.execute('SELECT clock_timestamp() AS at')
        boundary = cur.fetchone()['at']
        if old:
            cur.execute('''UPDATE core.media_member_post_process
                           SET is_current=FALSE, to_date=%s
                           WHERE vv_media_id=%s AND is_current=TRUE''', (boundary, news_id))
            if cur.rowcount != len(old):
                raise RuntimeError('Concurrent member replacement detected')
        for offset, entity in enumerate(sorted(prepared, key=lambda x: x['hash']), 1):
            columns = ['media_record_id','vv_media_id','media_version','extracted_member_id',
                       'member_payload','processor_manifest','member_hash','from_date','is_current']
            values = [expected['core_id'],news_id,expected['version'],last_id+offset,
                      Json(entity['payload']),Json(manifest),entity['hash'],boundary,True]
            if extra_columns:
                columns.extend(('entity_type','member_name'))
                values.extend((entity['entity_type'],entity['member_name']))
            placeholders = ','.join(['%s'] * len(values))
            cur.execute(f"INSERT INTO core.media_member_post_process ({','.join(columns)}) "
                        f"VALUES ({placeholders})", values)
        if expected['deleted']:
            return 'DELETED'
        if not expected['positive']:
            return 'NON_ADVERSE'
        if not prepared:
            return 'CLEARED' if old else 'EMPTY_UNCHANGED'
        return 'REPLACED' if old else 'INSERTED'
