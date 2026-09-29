"""To-do list of Media items that failed and must be retried by later runs."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repositories import jobStateRepository


MEDIA_TODO_KIND = "media"
_DONE_STATUSES = {"INSERTED", "SKIPPED"}


def load_items(dataset_name: str) -> list[dict[str, Any]]:
    state = jobStateRepository.load_state(MEDIA_TODO_KIND, dataset_name)
    return list((state or {}).get("items", {}).values())


def _candidate_keys(
    acquired: dict[str, Any],
    result: dict[str, Any],
) -> list[str]:
    extracted = acquired.get("extracted") or {}
    keys = (
        result.get("record_key"),
        acquired.get("record_key"),
        acquired.get("detail_url"),
        extracted.get("SourceURL"),
        acquired.get("detail_file_path"),
    )
    return [str(key) for key in keys if key]


def _retry_item(acquired: dict[str, Any]) -> dict[str, Any] | None:
    """Keep what a later run needs to retry one failed item."""

    detail_file_path = acquired.get("detail_file_path")

    if detail_file_path and Path(detail_file_path).is_file():
        return {
            "kind": "process",
            "record_key": acquired.get("record_key"),
            "detail_file_path": detail_file_path,
            "extracted": acquired.get("extracted"),
        }

    detail_url = acquired.get("detail_url") or (
        acquired.get("extracted") or {}
    ).get("SourceURL")

    if detail_url and acquired.get("record_key"):
        return {
            "kind": "fetch",
            "record_key": acquired["record_key"],
            "detail_url": detail_url,
            "source_record_id": acquired.get("source_record_id"),
            "identity_fields": acquired.get("identity_fields") or {},
        }

    return None


def update_items(
    dataset_name: str,
    acquired_records: list[dict[str, Any]],
    record_results: list[dict[str, Any]],
) -> dict[str, int]:
    """Remove items that reached Core; add or update items that failed."""

    state = jobStateRepository.load_state(MEDIA_TODO_KIND, dataset_name) or {
        "dataset_name": dataset_name,
        "items": {},
    }
    items = state["items"]
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    removed = 0
    unretryable = 0

    for acquired, result in zip(acquired_records, record_results):
        keys = _candidate_keys(acquired, result)

        if result.get("status") in _DONE_STATUSES:
            for key in keys:
                if items.pop(key, None) is not None:
                    removed += 1
            continue

        previous = next(
            (items.pop(key) for key in keys if key in items),
            {},
        )
        item = _retry_item(acquired) or (dict(previous) if previous else None)

        if item is None or not keys:
            unretryable += 1
            continue
        item.update(
            attempts=previous.get("attempts", 0) + 1,
            first_failed_at=previous.get("first_failed_at", now),
            last_failed_at=now,
            error=result.get("error"),
            error_stage=result.get("error_stage"),
        )
        items[keys[0]] = item
        added += 0 if previous else 1

    if items:
        jobStateRepository.save_state(MEDIA_TODO_KIND, dataset_name, state)
    else:
        jobStateRepository.delete_state(MEDIA_TODO_KIND, dataset_name)

    return {
        "todo_added_count": added,
        "todo_removed_count": removed,
        "todo_unretryable_count": unretryable,
        "todo_pending_count": len(items),
    }
