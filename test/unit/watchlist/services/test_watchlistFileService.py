"""
Unit tests for services/watchlistPipeline/watchlistFileService.py

Covers the raw.watchlist_file decisions the Data Insertion Guideline pins down:

  * Duplicate Detection (guideline "raw.watchlist_file" section 5)
      Case 1 -- no existing file          -> FIRST_DOWNLOAD
      Case 2 -- existing, same hash        -> exact duplicate (further split by
                                              how far that file already got:
                                              DUPLICATE_COMPLETED / RESUME_*)
      Case 3 -- existing, different hash   -> NEW_VERSION
  * File Versioning Strategy (section 6)
      continuous  -> every new hash bumps the version number (1, 2, 3, ...)
      independent -> no logical version (None)

The DB is replaced with mocks: we patch the connection pool and the
watchlistFileRepository lookups, then assert the status/version the service
derives from what the "database" returned.
"""

import os
from unittest.mock import MagicMock

import pytest

pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

from services.watchlistPipeline import watchlistFileService as fs  # noqa: E402

pytestmark = pytest.mark.unit


def _patch_latest_file(monkeypatch, return_value):
    monkeypatch.setattr(fs, "connection_pool", MagicMock())
    monkeypatch.setattr(
        fs.watchlistFileRepository,
        "find_latest_file",
        MagicMock(return_value=return_value),
    )


# --- Duplicate detection (section 5) ----------------------------------------

def test_first_download_when_no_existing_file(monkeypatch):
    _patch_latest_file(monkeypatch, None)

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="abc")

    assert result["duplicate_status"] == "FIRST_DOWNLOAD"


def test_different_hash_is_a_new_version(monkeypatch):
    _patch_latest_file(monkeypatch, {"file_hash": "OLD-HASH"})

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="NEW-HASH")

    assert result["duplicate_status"] == "NEW_VERSION"


def test_same_hash_already_normalized_is_completed_duplicate(monkeypatch):
    _patch_latest_file(monkeypatch, {
        "id": 50, "file_hash": "SAME", "file_version": "3", "storage_path": "p",
        "status": "PARSED", "has_raw_payloads": True, "normalization_completed": True,
    })

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="SAME")

    assert result["duplicate_status"] == "DUPLICATE_COMPLETED"
    assert result["watchlist_file_id"] == 50


def test_same_hash_with_raw_but_not_normalized_resumes_normalization(monkeypatch):
    _patch_latest_file(monkeypatch, {
        "id": 50, "file_hash": "SAME", "file_version": "3", "storage_path": "p",
        "status": "PARSED", "has_raw_payloads": True, "normalization_completed": False,
    })

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="SAME")

    assert result["duplicate_status"] == "RESUME_NORMALIZATION"


def test_same_hash_not_yet_parsed_resumes_processing(monkeypatch):
    _patch_latest_file(monkeypatch, {
        "id": 50, "file_hash": "SAME", "file_version": "3", "storage_path": "p",
        "status": "DOWNLOADED", "has_raw_payloads": False, "normalization_completed": False,
    })

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="SAME")

    assert result["duplicate_status"] == "RESUME_PROCESSING"


# --- File versioning (section 6) --------------------------------------------

def test_independent_lists_have_no_version(monkeypatch):
    result = fs.determine_file_version(
        config={"versioning_strategy": "independent"},
        duplicate_status="FIRST_DOWNLOAD",
        source_id=1,
        list_type_id=2,
    )
    assert result is None


def test_first_download_is_version_one(monkeypatch):
    result = fs.determine_file_version(
        config={"versioning_strategy": "continuous"},
        duplicate_status="FIRST_DOWNLOAD",
        source_id=1,
        list_type_id=2,
    )
    assert result == "1"


def test_new_version_increments_the_latest(monkeypatch):
    monkeypatch.setattr(fs, "connection_pool", MagicMock())
    monkeypatch.setattr(
        fs.watchlistFileRepository,
        "find_latest_file_version",
        MagicMock(return_value="3"),
    )

    result = fs.determine_file_version(
        config={"versioning_strategy": "continuous"},
        duplicate_status="NEW_VERSION",
        source_id=1,
        list_type_id=2,
    )
    assert result == "4"


def test_non_numeric_latest_version_raises(monkeypatch):
    monkeypatch.setattr(fs, "connection_pool", MagicMock())
    monkeypatch.setattr(
        fs.watchlistFileRepository,
        "find_latest_file_version",
        MagicMock(return_value="not-a-number"),
    )

    with pytest.raises(ValueError, match="Invalid latest file version"):
        fs.determine_file_version(
            config={"versioning_strategy": "continuous"},
            duplicate_status="NEW_VERSION",
            source_id=1,
            list_type_id=2,
        )
