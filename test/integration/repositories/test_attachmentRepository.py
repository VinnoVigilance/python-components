"""
Real-database tests for repositories/attachmentRepository.py.

The unit tests in test/unit/repositories/test_attachmentRepository.py use a fake
cursor: they prove each function sends the right SQL, but not that the SQL is
valid, that the foreign keys line up, or that the database enforces the rules
the guideline relies on.

These tests close that gap. They run the real INSERT/SELECT statements against a
real PostgreSQL loaded with the committed schema and assert the row state the
Attachment Management guideline requires -- including the store-once rule, which
is enforced by a UNIQUE constraint on file_hash that only a real database can
prove. Everything runs inside a transaction that is rolled back (see
test/integration/conftest.py), so nothing persists.

Run with:  TEST_DATABASE_URL=... pytest -m db
"""

import psycopg2
import psycopg2.errors
import pytest

from repositories import attachmentRepository as repo

pytestmark = [pytest.mark.integration, pytest.mark.db]


def _attachment_data(**overrides):
    data = {
        "storage_path": "bucket/obj",
        "file_name": "photo.jpg",
        "file_type": "jpg",
        "mime_type": "image/jpeg",
        "file_size": 1234,
        "file_hash": "ATT-HASH-1",
        "source_url": "http://src/photo.jpg",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# raw.attachment -- insert + find-by-hash round trip
# ---------------------------------------------------------------------------

def test_insert_attachment_is_found_back_by_hash(db_cursor):
    new_id = repo.insert_attachment(cursor=db_cursor, attachment_data=_attachment_data())
    assert new_id is not None

    found = repo.find_attachment_by_hash(cursor=db_cursor, file_hash="ATT-HASH-1")
    assert found["id"] == new_id
    assert found["file_name"] == "photo.jpg"
    assert found["file_hash"] == "ATT-HASH-1"
    assert found["source_url"] == "http://src/photo.jpg"


def test_find_attachment_by_hash_returns_none_when_absent(db_cursor):
    assert repo.find_attachment_by_hash(cursor=db_cursor, file_hash="NO-SUCH-HASH") is None


def test_duplicate_file_hash_is_rejected_by_the_database(db_cursor):
    """The store-once rule (guideline raw.attachment section 5) is guaranteed by
    a UNIQUE constraint on file_hash -- the database refuses a second row with
    the same hash, so a duplicate binary can never be registered twice. A fake
    cursor cannot prove this; a real one can."""
    repo.insert_attachment(cursor=db_cursor, attachment_data=_attachment_data(file_hash="DUP"))

    with pytest.raises(psycopg2.errors.UniqueViolation):
        repo.insert_attachment(
            cursor=db_cursor,
            attachment_data=_attachment_data(file_hash="DUP", storage_path="bucket/other"),
        )


# ---------------------------------------------------------------------------
# raw.list_attachment -- file -> attachment (real foreign keys)
# ---------------------------------------------------------------------------

def test_list_attachment_maps_file_to_attachment(db_cursor, seeded_parents):
    attachment_id = repo.insert_attachment(
        cursor=db_cursor, attachment_data=_attachment_data(file_hash="LIST-1"),
    )
    raw_file_id = seeded_parents["watchlist_file_id"]

    # Not mapped yet ...
    assert repo.find_list_attachment(
        cursor=db_cursor, raw_file_id=raw_file_id, attachment_id=attachment_id,
    ) is None

    mapping_id = repo.insert_list_attachment(
        cursor=db_cursor, raw_file_id=raw_file_id, attachment_id=attachment_id,
    )
    assert mapping_id is not None

    # ... and found afterwards. The FKs to raw.watchlist_file and raw.attachment
    # are real, so this also proves the mapping references valid parents.
    assert repo.find_list_attachment(
        cursor=db_cursor, raw_file_id=raw_file_id, attachment_id=attachment_id,
    ) == mapping_id


# ---------------------------------------------------------------------------
# raw.member_attachment -- entity -> attachment, keyed by external_id
# ---------------------------------------------------------------------------

def test_member_attachment_maps_entity_to_attachment(db_cursor):
    attachment_id = repo.insert_attachment(
        cursor=db_cursor, attachment_data=_attachment_data(file_hash="MEM-1"),
    )

    assert repo.find_member_attachment(
        cursor=db_cursor, external_id="A001", attachment_id=attachment_id, attachment_type="PHOTO",
    ) is None

    mapping_id = repo.insert_member_attachment(
        cursor=db_cursor, external_id="A001", attachment_id=attachment_id, attachment_type="PHOTO",
    )
    assert mapping_id is not None

    assert repo.find_member_attachment(
        cursor=db_cursor, external_id="A001", attachment_id=attachment_id, attachment_type="PHOTO",
    ) == mapping_id
    # A different attachment type is a distinct mapping, not a match.
    assert repo.find_member_attachment(
        cursor=db_cursor, external_id="A001", attachment_id=attachment_id, attachment_type="PASSPORT",
    ) is None


def test_one_attachment_can_map_to_many_entities(db_cursor):
    """Guideline: one attachment may be associated with multiple entities. Two
    member mappings referencing the same attachment_id must both be accepted."""
    attachment_id = repo.insert_attachment(
        cursor=db_cursor, attachment_data=_attachment_data(file_hash="SHARED"),
    )

    m1 = repo.insert_member_attachment(
        cursor=db_cursor, external_id="A001", attachment_id=attachment_id, attachment_type="PHOTO",
    )
    m2 = repo.insert_member_attachment(
        cursor=db_cursor, external_id="A002", attachment_id=attachment_id, attachment_type="PHOTO",
    )

    assert m1 != m2


def test_member_attachment_requires_a_real_attachment(db_cursor):
    """attachment_id is a real foreign key -- a mapping to a non-existent
    attachment is rejected by the database."""
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        repo.insert_member_attachment(
            cursor=db_cursor, external_id="A001", attachment_id=999_999_999, attachment_type="PHOTO",
        )
