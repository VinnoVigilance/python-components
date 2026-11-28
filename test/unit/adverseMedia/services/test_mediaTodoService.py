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


def test_queue_merges_existing_items_without_losing_failure_history():
    todo.update_items(DATASET, [_failed_fetch()], [{"status": "FAILED", "error": "timeout"}])
    previous = todo.load_items(DATASET)[0]

    todo.queue_items(DATASET, [
        {"record_key": "k1", "detail_url": "https://x/corrected"},
        {"record_key": "form:search1", "kind": "form_search", "parameters": {"number": "00042"}},
    ])

    items = {item["record_key"]: item for item in todo.load_items(DATASET)}
    assert set(items) == {"k1", "form:search1"}
    assert items["k1"] == {**previous, "detail_url": "https://x/corrected"}
    assert items["form:search1"]["parameters"] == {"number": "00042"}


def test_remove_search_preserves_publications_and_other_datasets():
    todo.queue_items(DATASET, [
        {"record_key": "search1", "kind": "form_search"},
        {"record_key": "pdf1", "kind": "process"},
    ])
    other = {"record_key": "search1", "kind": "form_search"}
    todo.queue_items("OTHER", [other])

    todo.remove_items(DATASET, ["search1", "absent"])

    assert todo.load_items(DATASET) == [{"record_key": "pdf1", "kind": "process"}]
    assert todo.load_items("OTHER") == [other]

    todo.remove_items(DATASET, ["pdf1"])
    assert jobStateRepository.load_state(todo.MEDIA_TODO_KIND, DATASET) is None
    todo.remove_items(DATASET, ["absent"])
    assert todo.load_items("OTHER") == [other]


@pytest.mark.parametrize("file_exists", [True, False])
def test_failed_pdf_retains_form_context_even_if_local_file_is_missing(tmp_path, file_exists):
    path = tmp_path / "publication.pdf"
    if file_exists:
        path.write_bytes(b"%PDF-original")
    context = {
        "kind": "form_search", "record_key": "form:search1",
        "recipe": {"start_url": "https://court.example/search"},
        "parameters": {"case_number": "00042"},
    }
    acquired = {
        "record_key": "pdf1", "detail_file_path": str(path),
        "extracted": {"Title": "Decision"}, "discovery_context": context,
    }

    todo.update_items(DATASET, [acquired], [{"status": "FAILED", "error": "processing failed"}])

    [item] = todo.load_items(DATASET)
    assert item["kind"] == "process"
    assert item["detail_file_path"] == str(path)
    assert item["extracted"] == acquired["extracted"]
    assert item["discovery_context"] == context


def test_pipeline_success_removes_a_publication_queued_during_acquisition():
    todo.queue_items(DATASET, [{"record_key": "pdf1", "kind": "process"}])

    counts = todo.update_items(DATASET, [{"record_key": "pdf1"}], [{"status": "INSERTED"}])

    assert counts["todo_removed_count"] == 1
    assert todo.load_items(DATASET) == []


@pytest.mark.parametrize("status", ["INSERTED", "SKIPPED"])
def test_metadata_success_keeps_attachment_failure_pending_until_documents_succeed(tmp_path, status):
    from utils.jsonlRecordBuffer import JsonlRecordBuffer

    records = JsonlRecordBuffer(tmp_path / "records.jsonl")
    try:
        records.append({"record_key": "entry", "retry_listing": True,
                        "extracted": {"SourceURL": "https://court.example/index", "DocumentErrors": []}})
        errors = [{"url": "https://court.example/order.pdf", "stage": "parse", "error": "OCR failed"}]
        counts = todo.update_items(DATASET, records, [
            {"record_key": "entry", "status": status, "document_errors": errors},
        ])
        [item] = todo.load_items(DATASET)
        assert item["kind"] == "list_only"
        assert item["error"] == errors
        assert counts["todo_pending_count"] == 1
        counts = todo.update_items(DATASET, records, [
            {"record_key": "entry", "status": status, "document_errors": []},
        ])
        assert counts["todo_removed_count"] == 1
        assert todo.load_items(DATASET) == []
    finally:
        records.close()
