"""Opt-in PostgreSQL integration checks on an EMPTY disposable *_test database.

Uses the repository's actual SQL. Does NOT connect without TEST_DATABASE_URL.
The project's integration conftest refuses production-looking names, and all
DDL/DML is rolled back. Never run in a populated production/test clone: this
fixture refuses existing core/delivery schemas.
"""
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]


def _make_empty_schema(db_connection):
    with db_connection.cursor() as cur:
        cur.execute("SELECT to_regnamespace('core'), to_regnamespace('delivery')")
        if any(cur.fetchone()):
            raise RuntimeError('Integration fixture requires an EMPTY disposable *_test database')
        cur.execute('''
            CREATE SCHEMA core;
            CREATE SCHEMA delivery;
            CREATE TABLE core.media_record (
                id bigint PRIMARY KEY, vv_media_id bigint NOT NULL, version_no int NOT NULL,
                is_current boolean NOT NULL, change_type text, media_payload jsonb NOT NULL
            );
            CREATE TABLE core.media_record_post_process (
                id bigint PRIMARY KEY, media_record_id bigint NOT NULL,
                vv_media_id bigint NOT NULL, media_version int NOT NULL,
                analysis_payload jsonb NOT NULL, post_process_hash text NOT NULL,
                is_current boolean NOT NULL DEFAULT true, to_date timestamptz,
                created_at timestamptz NOT NULL DEFAULT now()
            );
            CREATE TABLE core.media_member_post_process (
                id bigserial PRIMARY KEY,
                media_record_id bigint NOT NULL REFERENCES core.media_record(id),
                vv_media_id bigint NOT NULL, media_version int NOT NULL,
                extracted_member_id bigint NOT NULL, member_payload jsonb NOT NULL,
                processor_manifest jsonb NOT NULL, member_hash text NOT NULL,
                from_date timestamptz NOT NULL DEFAULT now(), to_date timestamptz,
                is_current boolean NOT NULL DEFAULT true,
                created_at timestamptz NOT NULL DEFAULT now(),
                CHECK (to_date IS NULL OR to_date >= from_date)
            );
            CREATE UNIQUE INDEX one_current_member
              ON core.media_member_post_process(media_record_id,extracted_member_id)
              WHERE is_current = true;
            CREATE TABLE delivery.media_daily_delta_actions (
              id bigint PRIMARY KEY, effective_date date NOT NULL, action text NOT NULL,
              vv_media_id bigint NOT NULL, media_record_id bigint NOT NULL
            );
            INSERT INTO core.media_record(id,vv_media_id,version_no,is_current,change_type,media_payload)
              VALUES (9001,410,1,true,'NEW', '{"Content":{"Title":"Alex","BodyText":"Body"}}');
            INSERT INTO core.media_record_post_process
              (id,media_record_id,vv_media_id,media_version,analysis_payload,post_process_hash,is_current)
              VALUES (7001,9001,410,1,'{"adversePrediction":{"status":"SCORED","modelPositive":true}}',
                       'a1',true);
        ''')


def _member(name='Alex', hashed='digest1'):
    return [{'hash':hashed,'payload':{'schemaVersion':'1.0','entityType':'INDIVIDUAL','memberName':name,'mentions':[{'text':name}]},
             'member_name':name,'entity_type':'INDIVIDUAL'}]


def _state(db_connection):
    with db_connection.cursor() as cur:
        cur.execute('''SELECT media_record_id, media_version, extracted_member_id,
                              member_hash, is_current, to_date
                       FROM core.media_member_post_process ORDER BY id''')
        return cur.fetchall()


def _replace(db_connection, current, members):
    from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repo
    return repo.save_members(db_connection, current, members, {'inputKey':'example'},
                             extra_columns=False)


@pytest.mark.parametrize('reprocess_variant', ['same','different'])
def test_full_history_version_change_and_hash(db_connection, reprocess_variant):
    from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repo
    _make_empty_schema(db_connection)
    assert repo.preflight(db_connection) is False
    initial = repo.snapshot(db_connection, 410)
    assert _replace(db_connection,initial,_member()) == 'INSERTED'
    assert _replace(db_connection,initial,_member()) == 'UNCHANGED'
    with db_connection.cursor() as cur:
        cur.execute('UPDATE core.media_record SET is_current=false WHERE id=9001')
        cur.execute('''INSERT INTO core.media_record VALUES
            (9008,410,2,true,'UPDATED','{"Content":{"Title":"Alex","BodyText":"Updated body"}}')''')
        # Article post process keeps old analysis row when its semantic hash is unchanged.
    current = repo.snapshot(db_connection,410)
    assert current['analysis_source_version']==1
    assert _replace(db_connection,current,_member()) == 'REPLACED'
    rows = _state(db_connection)
    assert [(r[0],r[1],r[2],r[4]) for r in rows] == [
        (9001,1,1,False),(9008,2,2,True)]
    assert rows[0][5] is not None and rows[1][5] is None
    current = repo.snapshot(db_connection,410)
    if reprocess_variant == 'same':
        assert _replace(db_connection,current,_member()) == 'UNCHANGED'
        assert len(_state(db_connection))==2
    else:
        assert _replace(db_connection,current,_member('Alex','newhash'))=='REPLACED'
        assert [(r[2],r[4]) for r in _state(db_connection)]==[(1,False),(2,False),(3,True)]


def test_negative_and_empty_expire_and_reappear(db_connection):
    from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repo
    _make_empty_schema(db_connection)
    current=repo.snapshot(db_connection,410)
    assert _replace(db_connection,current,_member())=='INSERTED'
    # A valid negative adverse decision closes old members without any NER.
    with db_connection.cursor() as cur:
        cur.execute('''UPDATE core.media_record_post_process
                       SET analysis_payload=jsonb_set(analysis_payload,
                         '{adversePrediction,modelPositive}','false'::jsonb),
                         post_process_hash='a2' WHERE id=7001''')
    negative=repo.snapshot(db_connection,410)
    assert negative['positive'] is False
    assert _replace(db_connection,negative,[])=='NON_ADVERSE'
    assert _state(db_connection)[0][4] is False
    assert _replace(db_connection,negative,[])=='EMPTY_UNCHANGED'
    with db_connection.cursor() as cur:
        cur.execute('''UPDATE core.media_record_post_process
                       SET analysis_payload=jsonb_set(analysis_payload,
                         '{adversePrediction,modelPositive}','true'::jsonb),
                         post_process_hash='a3' WHERE id=7001''')
    positive=repo.snapshot(db_connection,410)
    assert _replace(db_connection,positive,_member())=='INSERTED'
    assert [(r[2],r[4]) for r in _state(db_connection)] == [(1,False),(2,True)]
    assert _replace(db_connection,positive,[])=='CLEARED'
    assert _state(db_connection)[-1][4] is False


def test_insert_failure_rolls_back_entire_set(db_connection):
    from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repo
    _make_empty_schema(db_connection)
    current=repo.snapshot(db_connection,410)
    _replace(db_connection,current,_member())
    with db_connection.cursor() as cur:
        cur.execute('''ALTER TABLE core.media_member_post_process
                       ADD CONSTRAINT max_member_id_two CHECK (extracted_member_id < 2)''')
    with pytest.raises(Exception):
        with db_connection.cursor() as cur:
            cur.execute('SAVEPOINT before_bad_write')
        try:
            _replace(db_connection,current,_member('Alex','another_digest'))
        except Exception:
            with db_connection.cursor() as cur:
                cur.execute('ROLLBACK TO SAVEPOINT before_bad_write')
            raise
    assert [(r[2],r[4]) for r in _state(db_connection)] == [(1,True)]


def test_incremental_feeds_core_and_independent_analysis(db_connection):
    from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repo
    _make_empty_schema(db_connection)
    with db_connection.cursor() as cur:
        cur.execute("INSERT INTO delivery.media_daily_delta_actions VALUES (1,'2026-11-20','UPDATE',410,9001)")
    bounds=repo.scan_bounds(db_connection)
    found=repo.find_incremental_batch(db_connection,0,bounds[1],bounds[2],
                                      __import__('datetime').date(2026,11,20),
                                      __import__('datetime').date(2026,11,20),10)
    assert found==[410]


def test_verified_deleted_tombstone_closes_members_without_analysis(db_connection):
    from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repo
    _make_empty_schema(db_connection)
    current=repo.snapshot(db_connection,410)
    _replace(db_connection,current,_member())
    with db_connection.cursor() as cur:
        cur.execute("UPDATE core.media_record SET is_current=false, change_type='DELETED' WHERE id=9001")
    deleted=repo.snapshot(db_connection,410)
    assert deleted['deleted'] is True
    assert _replace(db_connection,deleted,[])=='DELETED'
    assert [(r[2],r[4]) for r in _state(db_connection)] == [(1,False)]
