"""
Unit tests for services/watchlistPipeline/watchlistRiskCategoryService.py

These pin down the exact behaviour the Data Insertion Guideline requires of the
Member Risk Category ETL ("Calculation & Versioning"):

  * Initial Load  -> every current member goes through the ADD workflow.
  * ADD / UPDATE   -> identical hash skips; different/absent hash expires the
                      current active row and inserts a new version.
  * DELETE         -> expire all active rows, insert nothing.
  * One active row -> an existing active row is always expired before insert.
  * Idempotency    -> a matching hash short-circuits, so re-runs are safe.

Every boundary (the pooled connection, the repository, the engine, the hash) is
mocked, so the tests assert the *decisions* without a database. The repository
calls are the observable effect of each decision.
"""

from unittest.mock import MagicMock

import pytest

from services.watchlistPipeline import (
    watchlistRiskCategoryService as svc,
)

pytestmark = pytest.mark.unit


def _engine(risk_details=None):
    engine = MagicMock()
    engine.classify.return_value = risk_details or {"RiskCategories": []}
    return engine


def _patch(monkeypatch, *, repo=None, new_hash="HASH-NEW"):
    """Patch the service boundaries; return the repo spy."""
    repo = repo or MagicMock()
    monkeypatch.setattr(svc, "connection_pool", MagicMock())
    monkeypatch.setattr(svc, "risk_repo", repo)
    monkeypatch.setattr(svc, "calculate_record_hash", MagicMock(return_value=new_hash))
    return repo


# ---------------------------------------------------------------------------
# Initial Load
# ---------------------------------------------------------------------------

def test_initial_load_inserts_new_member(monkeypatch):
    repo = MagicMock()
    # one page of members, then an empty page to end the loop
    repo.find_current_members_batch.side_effect = [
        [{"id": 10, "vv_member_id": "uuid-1", "version_no": 2, "full_payload": {}}],
        [],
    ]
    repo.find_current_risk.return_value = None  # nothing exists yet
    _patch(monkeypatch, repo=repo)

    result = svc.run_initial_load(engine=_engine())

    repo.insert_risk.assert_called_once()
    repo.expire_current_risk.assert_not_called()
    inserted = repo.insert_risk.call_args.args[1]
    assert inserted["vv_member_id"] == "uuid-1"
    assert inserted["watchlist_member_id"] == 10
    assert inserted["version_no"] == 2
    assert result["versioned_count"] == 1
    assert result["skipped_count"] == 0


def test_initial_load_is_idempotent_on_matching_hash(monkeypatch):
    # Re-running the load: the member already has an active row with the same
    # hash -> skip, no expire, no insert.
    repo = MagicMock()
    repo.find_current_members_batch.side_effect = [
        [{"id": 10, "vv_member_id": "uuid-1", "version_no": 2, "full_payload": {}}],
        [],
    ]
    repo.find_current_risk.return_value = {"id": 99, "risk_details_hash": "HASH-NEW"}
    _patch(monkeypatch, repo=repo, new_hash="HASH-NEW")

    result = svc.run_initial_load(engine=_engine())

    repo.insert_risk.assert_not_called()
    repo.expire_current_risk.assert_not_called()
    assert result["skipped_count"] == 1
    assert result["versioned_count"] == 0


# ---------------------------------------------------------------------------
# Incremental - ADD / UPDATE
# ---------------------------------------------------------------------------

def _delta(action, wm_id=10, vv="uuid-1"):
    return {"action": action, "vv_member_id": vv, "watchlist_member_id": wm_id}


def test_add_new_member_is_versioned(monkeypatch):
    repo = MagicMock()
    repo.find_max_effective_date.return_value = "2026-07-17"
    repo.find_delta_actions.return_value = [_delta("ADD")]
    repo.find_member_by_id.return_value = {
        "id": 10, "vv_member_id": "uuid-1", "version_no": 1, "full_payload": {},
    }
    repo.find_current_risk.return_value = None
    _patch(monkeypatch, repo=repo)

    result = svc.run_incremental(engine=_engine())

    repo.insert_risk.assert_called_once()
    repo.expire_current_risk.assert_not_called()
    assert result["effective_date"] == "2026-07-17"
    assert result["versioned_count"] == 1


def test_update_same_hash_is_skipped(monkeypatch):
    repo = MagicMock()
    repo.find_delta_actions.return_value = [_delta("UPDATE")]
    repo.find_member_by_id.return_value = {
        "id": 10, "vv_member_id": "uuid-1", "version_no": 3, "full_payload": {},
    }
    repo.find_current_risk.return_value = {"id": 99, "risk_details_hash": "HASH-NEW"}
    _patch(monkeypatch, repo=repo, new_hash="HASH-NEW")

    result = svc.run_incremental(effective_date="2026-07-17", engine=_engine())

    repo.insert_risk.assert_not_called()
    repo.expire_current_risk.assert_not_called()
    assert result["skipped_count"] == 1
    assert result["versioned_count"] == 0


def test_update_changed_hash_expires_then_inserts(monkeypatch):
    repo = MagicMock()
    repo.find_delta_actions.return_value = [_delta("UPDATE")]
    repo.find_member_by_id.return_value = {
        "id": 10, "vv_member_id": "uuid-1", "version_no": 4, "full_payload": {},
    }
    repo.find_current_risk.return_value = {"id": 99, "risk_details_hash": "HASH-OLD"}
    _patch(monkeypatch, repo=repo, new_hash="HASH-NEW")

    result = svc.run_incremental(effective_date="2026-07-17", engine=_engine())

    # Guideline: expire the current version, then insert the new one.
    repo.expire_current_risk.assert_called_once()
    assert repo.expire_current_risk.call_args.args[1] == "uuid-1"
    repo.insert_risk.assert_called_once()
    assert repo.insert_risk.call_args.args[1]["version_no"] == 4
    assert result["versioned_count"] == 1


def test_add_with_existing_active_row_still_expires_first(monkeypatch):
    # One-active-row invariant: even an ADD must expire a pre-existing active row
    # before inserting, so two current rows can never coexist.
    repo = MagicMock()
    repo.find_delta_actions.return_value = [_delta("ADD")]
    repo.find_member_by_id.return_value = {
        "id": 10, "vv_member_id": "uuid-1", "version_no": 2, "full_payload": {},
    }
    repo.find_current_risk.return_value = {"id": 99, "risk_details_hash": "HASH-OLD"}
    _patch(monkeypatch, repo=repo, new_hash="HASH-NEW")

    svc.run_incremental(effective_date="2026-07-17", engine=_engine())

    repo.expire_current_risk.assert_called_once()
    repo.insert_risk.assert_called_once()


# ---------------------------------------------------------------------------
# Incremental - DELETE
# ---------------------------------------------------------------------------

def test_delete_expires_active_rows_without_insert(monkeypatch):
    repo = MagicMock()
    repo.find_delta_actions.return_value = [_delta("DELETE")]
    repo.expire_current_risk.return_value = 1
    _patch(monkeypatch, repo=repo)

    result = svc.run_incremental(effective_date="2026-07-17", engine=_engine())

    repo.expire_current_risk.assert_called_once()
    repo.insert_risk.assert_not_called()
    repo.find_member_by_id.assert_not_called()
    assert result["deleted_count"] == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_missing_member_is_counted_not_fatal(monkeypatch):
    repo = MagicMock()
    repo.find_delta_actions.return_value = [_delta("UPDATE", wm_id=404)]
    repo.find_member_by_id.return_value = None  # referenced member is gone
    _patch(monkeypatch, repo=repo)

    result = svc.run_incremental(effective_date="2026-07-17", engine=_engine())

    repo.insert_risk.assert_not_called()
    assert result["missing_count"] == 1
    assert result["versioned_count"] == 0


def test_no_delta_data_completes_cleanly(monkeypatch):
    repo = MagicMock()
    repo.find_max_effective_date.return_value = None  # empty delta table
    _patch(monkeypatch, repo=repo)

    result = svc.run_incremental(engine=_engine())

    repo.find_delta_actions.assert_not_called()
    assert result["effective_date"] is None
    assert result["processed_count"] == 0
