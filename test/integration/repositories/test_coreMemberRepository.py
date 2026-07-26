"""
Real-database tests for repositories/coreMemberRepository.py.

The unit tests in test/unit/services/test_watchlistCoreService.py prove the
*decision* logic with a fake cursor (new vs. skip vs. update vs. delete). They
cannot prove that the SQL is valid, that the foreign keys line up, or that the
versioning/history columns end up in the right state -- because there is no
database.

These tests close that gap. They run the real INSERT/UPDATE statements against a
real PostgreSQL loaded with the committed schema, and assert the row state the
Data Insertion Guideline requires. Everything runs inside a transaction that is
rolled back (see test/integration/conftest.py), so nothing persists.

Run with:  TEST_DATABASE_URL=... pytest -m db
"""

import psycopg2
import psycopg2.errors
import pytest

from repositories import coreMemberRepository as repo

pytestmark = [pytest.mark.integration, pytest.mark.db]


def _read_member(cursor, member_id):
    cursor.execute(
        """
        SELECT version_no, is_current, change_type, valid_to, vv_member_id, record_hash
        FROM core.watchlist_member
        WHERE id = %s
        """,
        (member_id,),
    )
    row = cursor.fetchone()
    return {
        "version_no": row[0],
        "is_current": row[1],
        "change_type": row[2],
        "valid_to": row[3],
        "vv_member_id": row[4],
        "record_hash": row[5],
    }


# ---------------------------------------------------------------------------
# Insert a brand-new member (guideline: NEW -> version 1, current)
# ---------------------------------------------------------------------------


def test_insert_new_member_creates_version_one(db_cursor, make_member_data):
    result = repo.insert_new_member(cursor=db_cursor, member_data=make_member_data())

    assert result["version_no"] == 1
    assert result["vv_member_id"] is not None  # DEFAULT gen_random_uuid_v7() fired

    stored = _read_member(db_cursor, result["id"])
    assert stored["version_no"] == 1
    assert stored["is_current"] is True
    assert stored["change_type"] == "NEW"
    assert stored["valid_to"] is None


def test_find_current_member_returns_the_inserted_row(db_cursor, make_member_data, seeded_parents):
    inserted = repo.insert_new_member(cursor=db_cursor, member_data=make_member_data())

    found = repo.find_current_member(
        cursor=db_cursor,
        source_id=seeded_parents["source_id"],
        list_type_id=seeded_parents["list_type_id"],
        external_id="EXT-001",
    )

    assert found is not None
    assert found["id"] == inserted["id"]
    assert found["version_no"] == 1
    assert found["record_hash"] == "HASH-V1"


def test_find_current_member_returns_none_when_absent(db_cursor, seeded_parents):
    found = repo.find_current_member(
        cursor=db_cursor,
        source_id=seeded_parents["source_id"],
        list_type_id=seeded_parents["list_type_id"],
        external_id="DOES-NOT-EXIST",
    )
    assert found is None


# ---------------------------------------------------------------------------
# Update an existing member (guideline: changed -> close old, insert v+1)
# This is the versioning + history chain, end to end.
# ---------------------------------------------------------------------------


def test_update_closes_old_version_and_opens_the_next(db_cursor, make_member_data):
    v1 = repo.insert_new_member(cursor=db_cursor, member_data=make_member_data(record_hash="HASH-V1"))

    repo.close_current_member(cursor=db_cursor, core_member_id=v1["id"])
    v2 = repo.insert_updated_member(
        cursor=db_cursor,
        member_data=make_member_data(record_hash="HASH-V2"),
        vv_member_id=v1["vv_member_id"],
        version_no=v1["version_no"] + 1,
    )

    old = _read_member(db_cursor, v1["id"])
    new = _read_member(db_cursor, v2["id"])

    # The old version is retained as history: closed, not current, stamped.
    assert old["is_current"] is False
    assert old["valid_to"] is not None
    assert old["version_no"] == 1

    # The new version is current, v+1, same logical member (vv_member_id).
    assert new["is_current"] is True
    assert new["version_no"] == 2
    assert new["change_type"] == "UPDATED"
    assert new["vv_member_id"] == old["vv_member_id"]

    # And exactly one current version exists for that logical member.
    db_cursor.execute(
        """
        SELECT count(*) FROM core.watchlist_member
        WHERE vv_member_id = %s AND is_current = TRUE
        """,
        (old["vv_member_id"],),
    )
    assert db_cursor.fetchone()[0] == 1


def test_close_current_member_raises_if_nothing_to_close(db_cursor):
    # No row with this id -> rowcount 0 -> the repo must refuse silently passing.
    with pytest.raises(RuntimeError):
        repo.close_current_member(cursor=db_cursor, core_member_id=999_999_999)


def test_duplicate_version_number_is_rejected_by_unique_index(db_cursor, make_member_data):
    """uq_watchlist_member_vv_version guarantees (vv_member_id, version_no) is
    unique -- the safety net that stops two rows claiming the same version."""
    v1 = repo.insert_new_member(cursor=db_cursor, member_data=make_member_data())

    # Try to insert another version 1 for the same logical member.
    with pytest.raises(psycopg2.errors.UniqueViolation):
        repo.insert_updated_member(
            cursor=db_cursor,
            member_data=make_member_data(),
            vv_member_id=v1["vv_member_id"],
            version_no=1,
        )


# ---------------------------------------------------------------------------
# Delete detection (guideline: gone from source -> new DELETED version)
# ---------------------------------------------------------------------------


def test_delete_detection_records_a_deleted_version(db_cursor, make_member_data, seeded_parents):
    """The real delete-detection flow, end to end: a current member that is
    absent from the file is found, closed, and re-inserted as a DELETED version.

    This exercises the exact path the core service runs (find -> close -> insert),
    including reading full_payload back out of jsonb as a dict and writing it
    again -- the case that fails if the dict is not wrapped for the driver.
    """
    v1 = repo.insert_new_member(
        cursor=db_cursor, member_data=make_member_data(external_id="GONE-001")
    )

    gone = repo.find_deleted_current_members(
        cursor=db_cursor,
        source_id=seeded_parents["source_id"],
        list_type_id=seeded_parents["list_type_id"],
        watchlist_file_id=seeded_parents["watchlist_file_id"],
    )
    target = next(m for m in gone if m["external_id"] == "GONE-001")
    assert isinstance(target["full_payload"], dict)  # jsonb read back as a dict

    repo.close_current_member(cursor=db_cursor, core_member_id=v1["id"])
    deleted_id = repo.insert_deleted_member(
        cursor=db_cursor,
        current_member=target,
        watchlist_file_id=seeded_parents["watchlist_file_id"],
    )

    deleted = _read_member(db_cursor, deleted_id)
    assert deleted["change_type"] == "DELETED"
    assert deleted["version_no"] == 2
    assert deleted["is_current"] is True
    assert deleted["vv_member_id"] == v1["vv_member_id"]


def test_find_deleted_current_members_flags_rows_absent_from_the_file(
    db_cursor, make_member_data, seeded_parents
):
    # A current member whose external_id is NOT present in this file's raw
    # payloads should be reported as a candidate for deletion.
    repo.insert_new_member(cursor=db_cursor, member_data=make_member_data(external_id="ONLY-IN-CORE"))

    gone = repo.find_deleted_current_members(
        cursor=db_cursor,
        source_id=seeded_parents["source_id"],
        list_type_id=seeded_parents["list_type_id"],
        watchlist_file_id=seeded_parents["watchlist_file_id"],
    )

    external_ids = {m["external_id"] for m in gone}
    assert "ONLY-IN-CORE" in external_ids


# ---------------------------------------------------------------------------
# Rollback isolation, proven on a real member row.
# ---------------------------------------------------------------------------


def test_written_member_is_not_visible_on_a_second_connection(db_cursor, make_member_data, test_database_url):
    """What one test writes is invisible elsewhere until commit -- and these
    tests never commit. Proven across two independent connections."""
    inserted = repo.insert_new_member(cursor=db_cursor, member_data=make_member_data())

    other = psycopg2.connect(test_database_url)
    try:
        with other.cursor() as c:
            c.execute("SELECT count(*) FROM core.watchlist_member WHERE id = %s", (inserted["id"],))
            assert c.fetchone()[0] == 0
    finally:
        other.rollback()
        other.close()
