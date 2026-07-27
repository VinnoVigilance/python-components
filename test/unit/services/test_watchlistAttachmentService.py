"""
Unit tests for services/watchlistPipeline/watchlistAttachmentService.py

Covers the Attachment Management rules from the Data Insertion Guideline
(raw.attachment / raw.list_attachment / raw.member_attachment):

  * Duplicate Detection (raw.attachment section 5): a downloaded attachment is
    stored once. The same file hash is reused, never stored a second time.
  * Member vs. List scope: an attachment belonging to a source entity is mapped
    through raw.member_attachment (keyed by external_id); an attachment
    belonging to the whole file is mapped through raw.list_attachment.
  * Mapping records are not duplicated: an existing entity->attachment mapping is
    left alone.
  * Logging: an ATTACHMENT / STARTED event is written when processing begins and
    an ATTACHMENT / SUCCESS event when it finishes.
  * Error handling: a missing attachment file marks the watchlist file FAILED and
    re-raises.

Every I/O boundary (the pooled connection, the repositories, object storage, the
file-metadata helper) is mocked, so the tests assert the *decisions* the
guideline requires without a database or a storage backend. Real temp files are
used only so the service's on-disk existence check behaves realistically.
"""

import os
from unittest.mock import MagicMock

import pytest

pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

from services.watchlistPipeline import watchlistAttachmentService as att  # noqa: E402

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_file(tmp_path):
    """Build the year=/month=/day= layout the service reads for the object path."""
    day_dir = tmp_path / "year=2026" / "month=07" / "day=20"
    day_dir.mkdir(parents=True)
    source = day_dir / "list.html"
    source.write_text("source")
    return source, day_dir


def _make_file(day_dir, name, content="data"):
    path = day_dir / name
    path.write_text(content)
    return path


def _config(attachments):
    return {
        "source_name": "ATC",
        "list_name": "ATC-DESIGNATED-TERRORIST-INDIVIDUALS",
        "attachments": attachments,
    }


def _patch(monkeypatch, *, raw_records=(), repo=None, metadata_hash="HASH-1"):
    """Patch every boundary of the attachment service; return the repo spy."""
    repo = repo or MagicMock()
    monkeypatch.setattr(att, "connection_pool", MagicMock())
    monkeypatch.setattr(att, "attachmentRepository", repo)

    monkeypatch.setattr(
        att.rawPayloadRepository,
        "find_raw_payload_batch",
        MagicMock(side_effect=[list(raw_records), []]),
    )

    monkeypatch.setattr(att.watchlistFileService, "insert_file_log", MagicMock())
    monkeypatch.setattr(att.watchlistFileService, "mark_watchlist_file_as_failed", MagicMock())
    monkeypatch.setattr(
        att.watchlistFileService,
        "calculate_file_metadata",
        MagicMock(return_value={
            "file_hash": metadata_hash,
            "file_name": "photo.jpg",
            "file_type": "jpg",
            "mime_type": "image/jpeg",
            "file_size": 123,
        }),
    )
    monkeypatch.setattr(att.watchlistFileLogRepository, "insert_file_log", MagicMock())
    monkeypatch.setattr(att.seaweedClient, "upload_file", MagicMock(return_value="bucket/obj"))
    return repo


def _member_rule(local_path):
    return {
        "scope": "member",
        "attachment_type": "PHOTO",
        "local_path_field": "photo",
        "source_url_field": "photo_url",
    }, {"photo": str(local_path), "photo_url": "http://src/p.jpg"}


# ---------------------------------------------------------------------------
# No work
# ---------------------------------------------------------------------------

def test_no_attachment_rules_does_nothing(monkeypatch, tmp_path):
    source, _ = _source_file(tmp_path)
    repo = _patch(monkeypatch)

    result = att.process_attachments(
        source_file_path=source, watchlist_file_id=100, config=_config([]),
    )

    assert result == {
        "processed_count": 0, "new_count": 0, "reused_count": 0,
        "member_mapping_count": 0, "list_mapping_count": 0,
    }
    repo.insert_attachment.assert_not_called()
    # It returned before doing (or logging) any work.
    att.watchlistFileService.insert_file_log.assert_not_called()


# ---------------------------------------------------------------------------
# raw.attachment duplicate detection (guideline section 5)
# ---------------------------------------------------------------------------

def test_new_attachment_is_stored_and_registered(monkeypatch, tmp_path):
    source, day_dir = _source_file(tmp_path)
    photo = _make_file(day_dir, "p.jpg")
    rule, raw_json = _member_rule(photo)

    repo = _patch(monkeypatch, raw_records=[{"id": 1, "external_id": "A001", "raw_json": raw_json}])
    repo.find_attachment_by_hash.return_value = None       # new hash
    repo.insert_attachment.return_value = 500
    repo.find_member_attachment.return_value = None        # no mapping yet

    result = att.process_attachments(
        source_file_path=source, watchlist_file_id=100, config=_config([rule]),
    )

    # Stored once in object storage + registered once in raw.attachment ...
    att.seaweedClient.upload_file.assert_called_once()
    repo.insert_attachment.assert_called_once()
    # ... and mapped to the entity through raw.member_attachment.
    repo.insert_member_attachment.assert_called_once()
    assert repo.insert_member_attachment.call_args.kwargs["external_id"] == "A001"
    assert result["new_count"] == 1
    assert result["reused_count"] == 0
    assert result["member_mapping_count"] == 1
    assert result["processed_count"] == 1


def test_duplicate_attachment_is_reused_not_stored_again(monkeypatch, tmp_path):
    # Two entities publish a file with the SAME content hash. The binary must be
    # stored once; the second entity reuses the existing attachment record.
    source, day_dir = _source_file(tmp_path)
    photo1 = _make_file(day_dir, "p1.jpg")
    photo2 = _make_file(day_dir, "p2.jpg")
    rule = {
        "scope": "member", "attachment_type": "PHOTO",
        "local_path_field": "photo", "source_url_field": "photo_url",
    }
    records = [
        {"id": 1, "external_id": "A001", "raw_json": {"photo": str(photo1), "photo_url": "u1"}},
        {"id": 2, "external_id": "A002", "raw_json": {"photo": str(photo2), "photo_url": "u2"}},
    ]

    repo = _patch(monkeypatch, raw_records=records, metadata_hash="SAME-HASH")
    # first lookup: not found (store it); second lookup: found (reuse it).
    repo.find_attachment_by_hash.side_effect = [None, {"id": 500}]
    repo.insert_attachment.return_value = 500
    repo.find_member_attachment.return_value = None

    result = att.process_attachments(
        source_file_path=source, watchlist_file_id=100, config=_config([rule]),
    )

    # Physical file stored exactly once; the duplicate reused the record.
    att.seaweedClient.upload_file.assert_called_once()
    repo.insert_attachment.assert_called_once()
    assert result["new_count"] == 1
    assert result["reused_count"] == 1
    # One attachment associated with two entities -> two member mappings.
    assert repo.insert_member_attachment.call_count == 2
    assert result["member_mapping_count"] == 2


def test_existing_member_mapping_is_not_duplicated(monkeypatch, tmp_path):
    source, day_dir = _source_file(tmp_path)
    photo = _make_file(day_dir, "p.jpg")
    rule, raw_json = _member_rule(photo)

    repo = _patch(monkeypatch, raw_records=[{"id": 1, "external_id": "A001", "raw_json": raw_json}])
    repo.find_attachment_by_hash.return_value = {"id": 500}  # attachment already stored
    repo.find_member_attachment.return_value = 77            # mapping already exists

    result = att.process_attachments(
        source_file_path=source, watchlist_file_id=100, config=_config([rule]),
    )

    repo.insert_member_attachment.assert_not_called()
    assert result["reused_count"] == 1
    assert result["member_mapping_count"] == 0


# ---------------------------------------------------------------------------
# List scope (guideline: raw.list_attachment)
# ---------------------------------------------------------------------------

def test_list_scope_attachment_maps_to_the_file(monkeypatch, tmp_path):
    source, day_dir = _source_file(tmp_path)
    docs_dir = day_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "release_notes.pdf").write_text("notes")
    rule = {"scope": "list", "local_directory": "docs", "source_url": "http://src/notes.pdf"}

    repo = _patch(monkeypatch, raw_records=[])
    repo.find_attachment_by_hash.return_value = None
    repo.insert_attachment.return_value = 900
    repo.find_list_attachment.return_value = None

    result = att.process_attachments(
        source_file_path=source, watchlist_file_id=100, config=_config([rule]),
    )

    repo.insert_list_attachment.assert_called_once()
    kwargs = repo.insert_list_attachment.call_args.kwargs
    assert kwargs["raw_file_id"] == 100
    assert kwargs["attachment_id"] == 900
    assert result["list_mapping_count"] == 1
    assert result["new_count"] == 1


# ---------------------------------------------------------------------------
# Logging (guideline: file_log ATTACHMENT step)
# ---------------------------------------------------------------------------

def test_attachment_processing_is_logged_started_and_success(monkeypatch, tmp_path):
    source, day_dir = _source_file(tmp_path)
    photo = _make_file(day_dir, "p.jpg")
    rule, raw_json = _member_rule(photo)

    repo = _patch(monkeypatch, raw_records=[{"id": 1, "external_id": "A001", "raw_json": raw_json}])
    repo.find_attachment_by_hash.return_value = None
    repo.insert_attachment.return_value = 500
    repo.find_member_attachment.return_value = None

    att.process_attachments(
        source_file_path=source, watchlist_file_id=100, config=_config([rule]),
    )

    started = att.watchlistFileService.insert_file_log.call_args.kwargs
    assert started["step"] == "ATTACHMENT"
    assert started["status"] == "STARTED"

    finished = att.watchlistFileLogRepository.insert_file_log.call_args.kwargs
    assert finished["step"] == "ATTACHMENT"
    assert finished["status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_missing_attachment_file_marks_file_failed_and_raises(monkeypatch, tmp_path):
    source, _ = _source_file(tmp_path)
    # The raw record points at a file that does not exist on disk.
    rule = {
        "scope": "member", "attachment_type": "PHOTO",
        "local_path_field": "photo", "source_url_field": "photo_url",
    }
    raw_json = {"photo": str(tmp_path / "missing.jpg"), "photo_url": "u"}

    repo = _patch(monkeypatch, raw_records=[{"id": 1, "external_id": "A001", "raw_json": raw_json}])

    with pytest.raises(FileNotFoundError):
        att.process_attachments(
            source_file_path=source, watchlist_file_id=100, config=_config([rule]),
        )

    att.watchlistFileService.mark_watchlist_file_as_failed.assert_called_once()
    assert att.watchlistFileService.mark_watchlist_file_as_failed.call_args.kwargs["step"] == "ATTACHMENT"
    repo.insert_attachment.assert_not_called()
