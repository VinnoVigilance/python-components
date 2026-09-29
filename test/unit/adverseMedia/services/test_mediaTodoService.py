"""Unit tests for services/adverseMediaPipeline/mediaTodoService.py (failed-item to-do list)."""

import pytest

from repositories import jobStateRepository
from services.adverseMediaPipeline import mediaTodoService as todo

pytestmark = pytest.mark.unit

DATASET = "SEC_PH_ADVISORIES"


def _failed_fetch(key="k1", url="https://x/a"):
    return {"record_key": key, "detail_url": url, "source_record_id": "s1", "failed": True}


def test_failed_fetch_becomes_fetch_item():
    counts = todo.update_items(
        DATASET,
        [_failed_fetch()],
        [{"status": "FAILED", "error": "timeout", "error_stage": "DETAIL_FETCH"}],
    )

    assert counts == {
        "todo_added_count": 1, "todo_removed_count": 0,
        "todo_unretryable_count": 0, "todo_pending_count": 1,
    }
    [item] = todo.load_items(DATASET)
    assert item["kind"] == "fetch"
    assert item["detail_url"] == "https://x/a"
    assert item["attempts"] == 1
    assert item["error"] == "timeout"


def test_failed_item_with_saved_file_becomes_process_item(tmp_path):
    saved = tmp_path / "detail.html"
    saved.write_text("<html/>", encoding="utf-8")

    todo.update_items(
        DATASET,
        [{"record_key": "k1", "detail_file_path": str(saved), "extracted": {"Title": "t"}}],
        [{"status": "FAILED", "error": "db"}],
    )

    [item] = todo.load_items(DATASET)
    assert item["kind"] == "process"
    assert item["detail_file_path"] == str(saved)
    assert item["extracted"] == {"Title": "t"}


def test_failing_again_counts_attempts_and_keeps_first_failure():
    todo.update_items(DATASET, [_failed_fetch()], [{"status": "FAILED", "error": "one"}])
    first = todo.load_items(DATASET)[0]["first_failed_at"]

    counts = todo.update_items(DATASET, [_failed_fetch()], [{"status": "FAILED", "error": "two"}])

    [item] = todo.load_items(DATASET)
    assert item["attempts"] == 2
    assert item["error"] == "two"
    assert item["first_failed_at"] == first
    assert counts["todo_added_count"] == 0


def test_success_removes_item_and_deletes_empty_file():
    todo.update_items(DATASET, [_failed_fetch()], [{"status": "FAILED"}])

    counts = todo.update_items(DATASET, [_failed_fetch()], [{"status": "INSERTED"}])

    assert counts["todo_removed_count"] == 1
    assert counts["todo_pending_count"] == 0
    assert jobStateRepository.load_state(todo.MEDIA_TODO_KIND, DATASET) is None


def test_item_without_url_or_file_is_unretryable():
    counts = todo.update_items(DATASET, [{"record_key": "k1"}], [{"status": "FAILED"}])

    assert counts["todo_unretryable_count"] == 1
    assert todo.load_items(DATASET) == []


def test_successful_run_leaves_no_file():
    todo.update_items(DATASET, [_failed_fetch()], [{"status": "SKIPPED"}])

    assert jobStateRepository.list_state_names(todo.MEDIA_TODO_KIND) == []
