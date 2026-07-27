"""
Unit tests for repositories/attachmentRepository.py

Same safe idea as the other repository tests: no real database. We hand each
function a *fake* cursor (a Mock) and check that it sends the right SQL to the
right table with the right values, and returns what the database would hand
back.

These cover the three Raw-Layer attachment tables from the Data Insertion
Guideline (Attachment Management):
    raw.attachment          -- attachment metadata (deduplicated by file_hash)
    raw.list_attachment     -- file  -> attachment mapping
    raw.member_attachment   -- entity -> attachment mapping
"""

from unittest.mock import MagicMock

import pytest

from repositories import attachmentRepository

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# raw.attachment
# ---------------------------------------------------------------------------

def test_find_attachment_by_hash_returns_mapped_row():
    cursor = MagicMock()
    cursor.fetchone.return_value = (
        7, "path/x", "x.jpg", "jpg", "image/jpeg", 1024, "HASH", "http://x/x.jpg",
    )

    found = attachmentRepository.find_attachment_by_hash(cursor=cursor, file_hash="HASH")

    assert found == {
        "id": 7,
        "storage_path": "path/x",
        "file_name": "x.jpg",
        "file_type": "jpg",
        "mime_type": "image/jpeg",
        "file_size": 1024,
        "file_hash": "HASH",
        "source_url": "http://x/x.jpg",
    }
    # It looked the attachment up by hash, in the attachment table.
    sql, params = cursor.execute.call_args.args
    assert "raw.attachment" in sql
    assert params == ("HASH",)


def test_find_attachment_by_hash_returns_none_when_absent():
    cursor = MagicMock()
    cursor.fetchone.return_value = None

    assert attachmentRepository.find_attachment_by_hash(cursor=cursor, file_hash="NOPE") is None


def test_insert_attachment_passes_all_fields_and_returns_id():
    cursor = MagicMock()
    cursor.fetchone.return_value = (42,)

    new_id = attachmentRepository.insert_attachment(
        cursor=cursor,
        attachment_data={
            "storage_path": "bucket/obj",
            "file_name": "passport.png",
            "file_type": "png",
            "mime_type": "image/png",
            "file_size": 2048,
            "file_hash": "SHA256",
            "source_url": "http://src/passport.png",
        },
    )

    assert new_id == 42
    sql, params = cursor.execute.call_args.args
    assert "INSERT INTO raw.attachment" in sql
    assert params == (
        "bucket/obj", "passport.png", "png", "image/png", 2048, "SHA256",
        "http://src/passport.png",
    )


def test_insert_attachment_source_url_defaults_to_none():
    # source_url is optional -- a missing key must not raise, it becomes NULL.
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)

    attachmentRepository.insert_attachment(
        cursor=cursor,
        attachment_data={
            "storage_path": "bucket/obj",
            "file_name": "doc.pdf",
            "file_type": "pdf",
            "mime_type": "application/pdf",
            "file_size": 10,
            "file_hash": "H",
        },
    )

    _, params = cursor.execute.call_args.args
    assert params[-1] is None  # source_url


# ---------------------------------------------------------------------------
# raw.list_attachment (file -> attachment)
# ---------------------------------------------------------------------------

def test_find_list_attachment_returns_id_or_none():
    cursor = MagicMock()
    cursor.fetchone.return_value = (5,)
    assert attachmentRepository.find_list_attachment(cursor=cursor, raw_file_id=1, attachment_id=2) == 5

    cursor.fetchone.return_value = None
    assert attachmentRepository.find_list_attachment(cursor=cursor, raw_file_id=1, attachment_id=2) is None


def test_insert_list_attachment_maps_file_to_attachment():
    cursor = MagicMock()
    cursor.fetchone.return_value = (9,)

    new_id = attachmentRepository.insert_list_attachment(
        cursor=cursor, raw_file_id=100, attachment_id=42,
    )

    assert new_id == 9
    sql, params = cursor.execute.call_args.args
    assert "INSERT INTO raw.list_attachment" in sql
    assert params == (100, 42)


# ---------------------------------------------------------------------------
# raw.member_attachment (entity -> attachment, keyed by external_id)
# ---------------------------------------------------------------------------

def test_find_member_attachment_returns_id_or_none():
    cursor = MagicMock()
    cursor.fetchone.return_value = (8,)
    assert attachmentRepository.find_member_attachment(
        cursor=cursor, external_id="A001", attachment_id=42, attachment_type="PHOTO",
    ) == 8

    cursor.fetchone.return_value = None
    assert attachmentRepository.find_member_attachment(
        cursor=cursor, external_id="A001", attachment_id=42, attachment_type="PHOTO",
    ) is None


def test_insert_member_attachment_maps_entity_to_attachment():
    cursor = MagicMock()
    cursor.fetchone.return_value = (11,)

    new_id = attachmentRepository.insert_member_attachment(
        cursor=cursor, external_id="A001", attachment_id=42, attachment_type="PHOTO",
    )

    assert new_id == 11
    sql, params = cursor.execute.call_args.args
    assert "INSERT INTO raw.member_attachment" in sql
    # The mapping is keyed by the source's own external_id (guideline §7).
    assert params == ("A001", 42, "PHOTO")
