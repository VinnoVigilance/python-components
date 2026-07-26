"""
Unit tests for services/watchlistPipeline/watchlistCoreService.py

This is the Core Layer's change-detection logic, and it is exactly the behaviour
the Data Insertion Guideline pins down for core.watchlist_member:

  * Version Detection (guideline "core.watchlist_member" section 7)
      - New member            -> insert version 1, change_type NEW
      - Existing, same hash    -> no new version (SKIPPED)
      - Existing, changed hash -> close old version, insert version_no + 1 UPDATED
  * Delete Detection (section 8)
      - a current member no longer present in the new source dataset becomes a
        new DELETED version; only runs for `continuous` lists
  * Logging (section 10) -- a NORMALIZATION SUCCESS log is written at the end.

We drive the real process_watchlist_file() but replace every I/O boundary (the
DB connection, the repositories, the normalization engines, the hash) with
mocks, so the test asserts the *decisions* the guideline requires without a
database. The repository calls are the observable effect of each decision, so we
assert on which ones were made.
"""

import os
from unittest.mock import MagicMock

import pytest

# Importing the service pulls in the storage client (boto3) and settings.py.
# Mirror the e2e test's guard so this stays runnable in a lean environment.
pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

from services.watchlistPipeline import watchlistCoreService as core  # noqa: E402

pytestmark = pytest.mark.unit


def _setup(
    monkeypatch,
    *,
    raw_records,
    current_member,
    record_hash="HASH-NEW",
    deleted_members=(),
    versioning_strategy="continuous",
):
    """Patch every boundary of the core service and return the spy mocks.

    `raw_records`      -- one batch of raw payload rows the "DB" returns.
    `current_member`   -- what find_current_member returns for each raw record
                          (None = brand new; a dict = an existing current row).
    `record_hash`      -- the hash the normalized record produces.
    `deleted_members`  -- rows find_deleted_current_members returns.
    """
    # A fake pooled connection: `with connection:` and `connection.cursor()`
    # both work on a MagicMock, and the repositories are mocked so the cursor
    # itself is never really used.
    monkeypatch.setattr(core, "connection_pool", MagicMock())

    monkeypatch.setattr(
        core.watchlistNormalizationService,
        "create_normalization_engines",
        MagicMock(return_value=(MagicMock(), MagicMock(), MagicMock())),
    )
    monkeypatch.setattr(
        core.watchlistNormalizationService,
        "normalize_record",
        MagicMock(return_value={"EntityType": "Individual"}),
    )
    monkeypatch.setattr(core, "calculate_record_hash", MagicMock(return_value=record_hash))

    monkeypatch.setattr(core.watchlistFileService, "insert_file_log", MagicMock())
    monkeypatch.setattr(core.watchlistFileService, "mark_watchlist_file_as_failed", MagicMock())
    monkeypatch.setattr(core.watchlistFileLogRepository, "insert_file_log", MagicMock())

    repo = core.coreMemberRepository
    # first call returns the batch, second returns [] so the loop terminates.
    monkeypatch.setattr(
        core.rawPayloadRepository,
        "find_raw_payload_batch",
        MagicMock(side_effect=[list(raw_records), []]),
    )
    monkeypatch.setattr(repo, "find_current_member", MagicMock(return_value=current_member))
    monkeypatch.setattr(repo, "find_entity_type_id", MagicMock(return_value=5))
    monkeypatch.setattr(repo, "insert_new_member", MagicMock(return_value={"id": 1}))
    monkeypatch.setattr(repo, "insert_updated_member", MagicMock(return_value={"id": 2}))
    monkeypatch.setattr(repo, "close_current_member", MagicMock())
    monkeypatch.setattr(
        repo, "find_deleted_current_members", MagicMock(return_value=list(deleted_members))
    )
    monkeypatch.setattr(repo, "insert_deleted_member", MagicMock(return_value=9))

    config = {"versioning_strategy": versioning_strategy}
    return repo, config


def _run(config):
    return core.process_watchlist_file(
        watchlist_file_id=100,
        source_id=1,
        list_type_id=2,
        config=config,
    )


def test_new_member_is_inserted_as_version_one(monkeypatch):
    repo, config = _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": "A001", "raw_json": {}}],
        current_member=None,  # nothing exists yet -> NEW
    )

    result = _run(config)

    repo.insert_new_member.assert_called_once()
    repo.insert_updated_member.assert_not_called()
    assert result["new_count"] == 1
    assert result["updated_count"] == 0
    assert result["skipped_count"] == 0
    assert result["processed_count"] == 1


def test_unchanged_member_is_skipped_no_new_version(monkeypatch):
    # Existing current version with the SAME hash -> no new version at all.
    repo, config = _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": "A001", "raw_json": {}}],
        current_member={"id": 55, "vv_member_id": "uuid-1", "version_no": 3, "record_hash": "HASH-NEW"},
        record_hash="HASH-NEW",
    )

    result = _run(config)

    repo.insert_new_member.assert_not_called()
    repo.insert_updated_member.assert_not_called()
    repo.close_current_member.assert_not_called()
    assert result["skipped_count"] == 1
    assert result["new_count"] == 0
    assert result["updated_count"] == 0


def test_changed_member_closes_old_and_inserts_next_version(monkeypatch):
    # Existing current version with a DIFFERENT hash -> UPDATED.
    repo, config = _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": "A001", "raw_json": {}}],
        current_member={"id": 55, "vv_member_id": "uuid-1", "version_no": 3, "record_hash": "HASH-OLD"},
        record_hash="HASH-NEW",
    )

    result = _run(config)

    # The previous current version is closed ...
    repo.close_current_member.assert_called_once()
    assert repo.close_current_member.call_args.kwargs["core_member_id"] == 55
    # ... and the new version is version_no + 1, keeping the same vv_member_id.
    repo.insert_updated_member.assert_called_once()
    kwargs = repo.insert_updated_member.call_args.kwargs
    assert kwargs["version_no"] == 4
    assert kwargs["vv_member_id"] == "uuid-1"
    assert result["updated_count"] == 1
    assert result["new_count"] == 0


def test_missing_external_member_becomes_deleted_version(monkeypatch):
    # The one incoming record is new; separately, a current member that is no
    # longer in the source is detected and turned into a DELETED version.
    gone = {
        "id": 77, "vv_member_id": "uuid-9", "source_id": 1, "list_type_id": 2,
        "external_id": "GONE", "entity_type_id": 5, "version_no": 2,
        "record_hash": "H", "full_payload": {},
    }
    repo, config = _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": "A001", "raw_json": {}}],
        current_member=None,
        deleted_members=[gone],
        versioning_strategy="continuous",
    )

    result = _run(config)

    repo.find_deleted_current_members.assert_called_once()
    repo.insert_deleted_member.assert_called_once()
    assert repo.close_current_member.call_args.kwargs["core_member_id"] == 77
    assert result["deleted_count"] == 1


def test_delete_detection_skipped_for_non_continuous_lists(monkeypatch):
    repo, config = _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": "A001", "raw_json": {}}],
        current_member=None,
        deleted_members=[{"id": 77}],  # would be deleted IF detection ran
        versioning_strategy="independent",
    )

    result = _run(config)

    repo.find_deleted_current_members.assert_not_called()
    repo.insert_deleted_member.assert_not_called()
    assert result["deleted_count"] == 0


def test_success_is_logged_at_the_end(monkeypatch):
    _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": "A001", "raw_json": {}}],
        current_member=None,
    )

    _run({"versioning_strategy": "continuous"})

    # A NORMALIZATION SUCCESS log row must be written (guideline section 10).
    logged = core.watchlistFileLogRepository.insert_file_log.call_args.kwargs
    assert logged["step"] == "NORMALIZATION"
    assert logged["status"] == "SUCCESS"


def test_missing_entity_type_fails_and_marks_file_failed(monkeypatch):
    repo, config = _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": "A001", "raw_json": {}}],
        current_member=None,
    )
    # Normalization yields no EntityType -> the core service must refuse it.
    core.watchlistNormalizationService.normalize_record.return_value = {"EntityType": ""}

    with pytest.raises(ValueError, match="EntityType"):
        _run(config)

    core.watchlistFileService.mark_watchlist_file_as_failed.assert_called_once()
