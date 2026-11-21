"""
Unit tests for services/watchlistPipeline/watchlistCoreService.py

This is the Core Layer's change-detection logic:

- New member -> insert version 1 with change_type NEW
- Existing member with the same hash -> SKIPPED
- Existing member with a different hash -> UPDATED
- Deleted member seen again -> UPDATED and active again
- Missing member from a continuous source -> DELETED
"""

import os
from unittest.mock import MagicMock

import pytest


pytest.importorskip(
    "boto3",
    reason="full pipeline stack (boto3) not installed",
)

os.environ.setdefault(
    "DB_PASSWORD",
    "test_dummy",
)

os.environ.setdefault(
    "STORAGE_SECRET_KEY_INGESTION",
    "test_dummy",
)


from services.watchlistPipeline import (  # noqa: E402
    watchlistCoreService as core,
)


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
    """
    Replace all external dependencies with mocks.

    raw_records:
        Raw records returned from PostgreSQL.

    current_member:
        The current Core member. None means the member is new.

    record_hash:
        The hash of the normalized incoming record.

    deleted_members:
        Active members that are no longer present in the source.
    """

    monkeypatch.setattr(
        core,
        "connection_pool",
        MagicMock(),
    )

    monkeypatch.setattr(
        core.watchlistNormalizationService,
        "create_normalization_engines",
        MagicMock(
            return_value=(
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )
        ),
    )

    monkeypatch.setattr(
        core.watchlistNormalizationService,
        "normalize_record",
        MagicMock(
            return_value={
                "EntityType": "Individual",
            }
        ),
    )

    monkeypatch.setattr(
        core,
        "calculate_record_hash",
        MagicMock(
            return_value=record_hash,
        ),
    )

    monkeypatch.setattr(
        core.watchlistFileService,
        "insert_file_log",
        MagicMock(),
    )

    monkeypatch.setattr(
        core.watchlistFileService,
        "mark_watchlist_file_as_failed",
        MagicMock(),
    )

    monkeypatch.setattr(
        core.watchlistFileLogRepository,
        "insert_file_log",
        MagicMock(),
    )

    repository = core.coreMemberRepository

    monkeypatch.setattr(
        core.rawPayloadRepository,
        "find_raw_payload_batch",
        MagicMock(
            side_effect=[
                list(raw_records),
                [],
            ]
        ),
    )

    monkeypatch.setattr(
        repository,
        "find_current_member",
        MagicMock(
            return_value=current_member,
        ),
    )

    monkeypatch.setattr(
        repository,
        "find_entity_type_id",
        MagicMock(
            return_value=5,
        ),
    )

    monkeypatch.setattr(
        repository,
        "insert_new_member",
        MagicMock(
            return_value={
                "id": 1,
            }
        ),
    )

    monkeypatch.setattr(
        repository,
        "insert_updated_member",
        MagicMock(
            return_value={
                "id": 2,
            }
        ),
    )

    monkeypatch.setattr(
        repository,
        "close_current_member",
        MagicMock(),
    )

    monkeypatch.setattr(
        repository,
        "find_deleted_current_members",
        MagicMock(
            return_value=list(deleted_members),
        ),
    )

    monkeypatch.setattr(
        repository,
        "insert_deleted_member",
        MagicMock(
            return_value=9,
        ),
    )

    config = {
        "versioning_strategy": versioning_strategy,
    }

    return repository, config


def _run(
    config,
):
    return core.process_watchlist_file(
        watchlist_file_id=100,
        source_id=1,
        list_type_id=2,
        config=config,
    )


def test_new_member_is_inserted_as_version_one(
    monkeypatch,
):
    repository, config = _setup(
        monkeypatch,
        raw_records=[
            {
                "id": 11,
                "external_id": "A001",
                "raw_json": {},
            }
        ],
        current_member=None,
    )

    result = _run(config)

    repository.insert_new_member.assert_called_once()
    repository.insert_updated_member.assert_not_called()
    repository.close_current_member.assert_not_called()

    assert result["new_count"] == 1
    assert result["updated_count"] == 0
    assert result["skipped_count"] == 0
    assert result["processed_count"] == 1


def test_unchanged_active_member_is_skipped(
    monkeypatch,
):
    repository, config = _setup(
        monkeypatch,
        raw_records=[
            {
                "id": 11,
                "external_id": "A001",
                "raw_json": {},
            }
        ],
        current_member={
            "id": 55,
            "vv_member_id": "uuid-1",
            "version_no": 3,
            "record_hash": "HASH-NEW",
            "change_type": "UPDATED",
        },
        record_hash="HASH-NEW",
    )

    result = _run(config)

    repository.insert_new_member.assert_not_called()
    repository.insert_updated_member.assert_not_called()
    repository.close_current_member.assert_not_called()

    assert result["skipped_count"] == 1
    assert result["new_count"] == 0
    assert result["updated_count"] == 0


def test_changed_member_creates_next_version(
    monkeypatch,
):
    repository, config = _setup(
        monkeypatch,
        raw_records=[
            {
                "id": 11,
                "external_id": "A001",
                "raw_json": {},
            }
        ],
        current_member={
            "id": 55,
            "vv_member_id": "uuid-1",
            "version_no": 3,
            "record_hash": "HASH-OLD",
            "change_type": "UPDATED",
        },
        record_hash="HASH-NEW",
    )

    result = _run(config)

    repository.close_current_member.assert_called_once()

    close_arguments = (
        repository.close_current_member.call_args.kwargs
    )

    assert close_arguments["core_member_id"] == 55

    repository.insert_updated_member.assert_called_once()

    update_arguments = (
        repository.insert_updated_member.call_args.kwargs
    )

    assert update_arguments["version_no"] == 4
    assert update_arguments["vv_member_id"] == "uuid-1"

    assert result["updated_count"] == 1
    assert result["new_count"] == 0
    assert result["skipped_count"] == 0


def test_deleted_member_seen_again_is_reactivated_even_with_same_hash(
    monkeypatch,
):
    repository, config = _setup(
        monkeypatch,
        raw_records=[
            {
                "id": 11,
                "external_id": "A001",
                "raw_json": {},
            }
        ],
        current_member={
            "id": 55,
            "vv_member_id": "uuid-1",
            "version_no": 3,
            "record_hash": "HASH-NEW",
            "change_type": "DELETED",
        },
        record_hash="HASH-NEW",
    )

    result = _run(config)

    repository.close_current_member.assert_called_once()

    close_arguments = (
        repository.close_current_member.call_args.kwargs
    )

    assert close_arguments["core_member_id"] == 55

    repository.insert_updated_member.assert_called_once()

    update_arguments = (
        repository.insert_updated_member.call_args.kwargs
    )

    assert update_arguments["vv_member_id"] == "uuid-1"
    assert update_arguments["version_no"] == 4

    assert result["updated_count"] == 1
    assert result["skipped_count"] == 0


def test_missing_external_member_becomes_deleted_version(
    monkeypatch,
):
    gone_member = {
        "id": 77,
        "vv_member_id": "uuid-9",
        "source_id": 1,
        "list_type_id": 2,
        "external_id": "GONE",
        "entity_type_id": 5,
        "version_no": 2,
        "record_hash": "HASH-GONE",
        "full_payload": {},
    }

    repository, config = _setup(
        monkeypatch,
        raw_records=[
            {
                "id": 11,
                "external_id": "A001",
                "raw_json": {},
            }
        ],
        current_member=None,
        deleted_members=[
            gone_member,
        ],
        versioning_strategy="continuous",
    )

    result = _run(config)

    (
        repository.find_deleted_current_members
        .assert_called_once()
    )

    repository.insert_deleted_member.assert_called_once()

    close_arguments = (
        repository.close_current_member.call_args.kwargs
    )

    assert close_arguments["core_member_id"] == 77
    assert result["deleted_count"] == 1


def test_delete_detection_is_skipped_for_non_continuous_lists(
    monkeypatch,
):
    repository, config = _setup(
        monkeypatch,
        raw_records=[
            {
                "id": 11,
                "external_id": "A001",
                "raw_json": {},
            }
        ],
        current_member=None,
        deleted_members=[
            {
                "id": 77,
            }
        ],
        versioning_strategy="independent",
    )

    result = _run(config)

    (
        repository.find_deleted_current_members
        .assert_not_called()
    )

    repository.insert_deleted_member.assert_not_called()

    assert result["deleted_count"] == 0


def test_dbm_config_retires_assignment_absent_from_new_directory(monkeypatch):
    from pipelines.watchlistConfigs import WATCHLIST_CONFIGS

    gone_member = {
        "id": 77, "vv_member_id": "old-assignment", "source_id": 1,
        "list_type_id": 2, "external_id": "OLD-POSITION",
        "entity_type_id": 5, "version_no": 2,
        "record_hash": "OLD-HASH", "full_payload": {},
    }
    repository, _ = _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": "NEW-POSITION", "raw_json": {}}],
        current_member=None,
        deleted_members=[gone_member],
    )

    result = _run(WATCHLIST_CONFIGS["DBM_PH_GOVERNMENT_OFFICIALS_DIRECTORY"])

    assert result["new_count"] == 1
    assert result["deleted_count"] == 1
    repository.close_current_member.assert_called_once_with(
        cursor=repository.close_current_member.call_args.kwargs["cursor"],
        core_member_id=77,
    )
    deletion = repository.insert_deleted_member.call_args.kwargs
    assert deletion["current_member"] == gone_member
    assert deletion["watchlist_file_id"] == 100


@pytest.mark.parametrize("failure", ["empty", "normalization"])
def test_dbm_does_not_retire_assignments_when_core_processing_fails(monkeypatch, failure):
    from pipelines.watchlistConfigs import WATCHLIST_CONFIGS

    repository, _ = _setup(
        monkeypatch,
        raw_records=[] if failure == "empty" else [
            {"id": 11, "external_id": "PRESENT", "raw_json": {}}
        ],
        current_member=None,
    )
    if failure == "normalization":
        core.watchlistNormalizationService.normalize_record.side_effect = ValueError("invalid record")

    with pytest.raises(ValueError):
        _run(WATCHLIST_CONFIGS["DBM_PH_GOVERNMENT_OFFICIALS_DIRECTORY"])

    repository.find_deleted_current_members.assert_not_called()
    repository.insert_deleted_member.assert_not_called()


def test_dbm_retiring_one_position_does_not_retire_same_person_other_position(monkeypatch):
    from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
    from transforms.preProcessingEngine import PreProcessingEngine

    config = WATCHLIST_CONFIGS["DBM_PH_GOVERNMENT_OFFICIALS_DIRECTORY"]
    assignments = PreProcessingEngine().preprocess([
        {"record_kind": "listing", "printed_name": "SAME OFFICIAL", "role": role,
         "source_path": ["national_government", "records", index]}
        for index, role in enumerate(("Secretary", "Chairperson"))
    ], config["preprocessing"])
    retained, removed = assignments
    assert retained["source_record_id"] != removed["source_record_id"]
    gone_member = {
        "id": 77, "vv_member_id": "removed-assignment", "source_id": 1,
        "list_type_id": 2, "external_id": removed["source_record_id"],
        "entity_type_id": 5, "version_no": 2,
        "record_hash": "OLD-HASH", "full_payload": {},
    }
    repository, _ = _setup(
        monkeypatch,
        raw_records=[{"id": 11, "external_id": retained["source_record_id"], "raw_json": retained}],
        current_member={"id": 88, "change_type": "NEW", "record_hash": "HASH-NEW"},
        deleted_members=[gone_member],
    )
    result = _run(config)
    assert result["skipped_count"] == 1
    assert result["deleted_count"] == 1
    assert repository.close_current_member.call_args.kwargs["core_member_id"] == 77
    assert repository.close_current_member.call_count == 1
    assert repository.insert_deleted_member.call_args.kwargs["current_member"]["external_id"] == removed["source_record_id"]


def test_success_is_logged_at_the_end(
    monkeypatch,
):
    _setup(
        monkeypatch,
        raw_records=[
            {
                "id": 11,
                "external_id": "A001",
                "raw_json": {},
            }
        ],
        current_member=None,
    )

    _run(
        {
            "versioning_strategy": "continuous",
        }
    )

    logged_arguments = (
        core.watchlistFileLogRepository
        .insert_file_log.call_args.kwargs
    )

    assert logged_arguments["step"] == "NORMALIZATION"
    assert logged_arguments["status"] == "SUCCESS"


def test_missing_entity_type_fails_and_marks_file_failed(
    monkeypatch,
):
    _, config = _setup(
        monkeypatch,
        raw_records=[
            {
                "id": 11,
                "external_id": "A001",
                "raw_json": {},
            }
        ],
        current_member=None,
    )

    (
        core.watchlistNormalizationService
        .normalize_record.return_value
    ) = {
        "EntityType": "",
    }

    with pytest.raises(
        ValueError,
        match="EntityType",
    ):
        _run(config)

    (
        core.watchlistFileService
        .mark_watchlist_file_as_failed
        .assert_called_once()
    )
