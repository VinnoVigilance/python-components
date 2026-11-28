import os
import logging
import threading
import time
from copy import deepcopy

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault(
    "STORAGE_SECRET_KEY_INGESTION",
    "test_dummy",
)

pytest.importorskip(
    "boto3",
    reason="Media storage stack is not installed.",
)

from services.adverseMediaPipeline.mediaAcquisitionService import (
    MediaAcquisitionService,
)


pytestmark = pytest.mark.unit


def _crawler_config():
    return {
        "source_name": "SRC",
        "dataset_name": "DATA",
        "url": "https://example.test/news",
    }


@pytest.mark.parametrize(
    ("mode", "policy", "expected_stop"),
    [
        ("INITIAL", "stop_after_known", False),
        ("INCREMENTAL", "stop_after_known", True),
        ("INCREMENTAL", "full_scan", False),
    ],
)
def test_mode_and_policy_control_known_record_stop(
    monkeypatch,
    mode,
    policy,
    expected_stop,
):
    crawl_source = MagicMock(
        return_value=SimpleNamespace(records=[])
    )
    monkeypatch.setattr(
        MediaAcquisitionService,
        "_crawl_source",
        crawl_source,
    )

    result = MediaAcquisitionService._acquire_source(
        source_config=_crawler_config(),
        acquisition_type="crawler",
        source_id=1,
        dataset_id=2,
        mode=mode,
        discovery_policy=policy,
        known_threshold=10,
    )

    assert result.records == []
    assert (
        crawl_source.call_args.kwargs[
            "stop_after_known"
        ]
        is expected_stop
    )


@pytest.mark.parametrize(
    ("mode", "policy", "expected_stop"),
    [
        ("INITIAL", "stop_after_known", False),
        ("INCREMENTAL", "stop_after_known", True),
        ("INCREMENTAL", "full_scan", False),
    ],
)
def test_mode_and_policy_control_known_record_stop_for_api(
    monkeypatch,
    mode,
    policy,
    expected_stop,
):
    collect_api_source = MagicMock(
        return_value=SimpleNamespace(records=[])
    )
    monkeypatch.setattr(
        MediaAcquisitionService,
        "_collect_api_source",
        collect_api_source,
    )

    result = MediaAcquisitionService._acquire_source(
        source_config=_crawler_config(),
        acquisition_type="api",
        source_id=1,
        dataset_id=2,
        mode=mode,
        discovery_policy=policy,
        known_threshold=10,
    )

    assert result.records == []
    assert (
        collect_api_source.call_args.kwargs[
            "stop_after_known"
        ]
        is expected_stop
    )


def _persist_with_fakes(monkeypatch, record):
    import services.adverseMediaPipeline.mediaAcquisitionService as module

    seen = {}

    def build_file_metadata(file_path, file_url):
        seen["file_url"] = file_url
        return {"file_path": file_path, "file_url": file_url}

    monkeypatch.setattr(module.MediaFileService, "build_file_metadata", staticmethod(build_file_metadata))
    monkeypatch.setattr(
        module.MediaRawService,
        "register_file",
        staticmethod(lambda **kwargs: {"media_file_id": 1, "file_hash": "h", "storage_path": "s3://x", "status": "DOWNLOADED", "is_duplicate": False}),
    )

    seen["record"] = MediaAcquisitionService._persist_record(
        record=record,
        source_id=1,
        dataset_id=2,
        source_name="SRC",
        dataset_name="DS",
        download_method="BYPASS",
        acquisition_url="https://example.com/list",
    )

    return seen


def test_downloaded_document_url_is_the_raw_file_url(monkeypatch):
    record = {
        "detail_file_path": "doc_0.pdf",
        "file_url": "https://example.com/doc.pdf",
        "extracted": {"SourceURL": "https://example.com/article/"},
    }

    assert _persist_with_fakes(monkeypatch, record)["file_url"] == "https://example.com/doc.pdf"


def test_article_url_is_the_raw_file_url_without_a_document(monkeypatch):
    record = {
        "detail_file_path": "article.html",
        "extracted": {"SourceURL": "https://example.com/article/"},
    }

    assert _persist_with_fakes(monkeypatch, record)["file_url"] == "https://example.com/article/"


def test_retry_mode_rebuilds_records_from_the_todo_list(monkeypatch):
    from repositories import jobStateRepository
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    jobStateRepository.save_state("media", "DATA", {"items": {
        "k1": {"kind": "process", "record_key": "k1",
               "detail_file_path": "k1.html", "extracted": {"Title": "t"}},
        "k2": {"kind": "fetch", "record_key": "k2", "detail_url": "https://example.test/k2"},
    }})
    crawl = MagicMock(return_value=SimpleNamespace(records=[{"record_key": "k2"}]))
    monkeypatch.setattr(module, "crawl", crawl)

    result = MediaAcquisitionService._acquire_todo_items(_crawler_config())

    task = crawl.call_args.kwargs["task"]
    assert [item["record_key"] for item in task.detail_items] == ["k2"]
    assert [record["record_key"] for record in result.records] == ["k1", "k2"]
    assert result.records[0]["detail_file_path"] == "k1.html"
    assert result.stop_reason == "TODO_RETRY"


def test_retry_mode_without_fetch_items_does_not_crawl(monkeypatch):
    from repositories import jobStateRepository
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    jobStateRepository.save_state("media", "DATA", {"items": {
        "k1": {"kind": "process", "record_key": "k1", "detail_file_path": "k1.html"},
    }})
    crawl = MagicMock()
    monkeypatch.setattr(module, "crawl", crawl)

    result = MediaAcquisitionService._acquire_todo_items(_crawler_config())

    crawl.assert_not_called()
    assert len(result.records) == 1


def test_pdf_url_fallback_and_navigation_context_survive_persistence(monkeypatch):
    context = {"kind": "form_search", "record_key": "search", "recipe": {"start_url": "https://example.test/search"}}
    record = {"record_key": "publication", "detail_file_path": "decision.pdf",
              "extracted": {"PDFURL": "https://example.test/decision.pdf", "SourceURL": "https://example.test/search"},
              "discovery_context": context, "discovery_retry_item": context}

    seen = _persist_with_fakes(monkeypatch, record)

    assert seen["file_url"] == record["extracted"]["PDFURL"]
    assert seen["record"]["discovery_context"] == context
    assert seen["record"]["discovery_retry_item"] == context


def test_explicit_file_url_takes_precedence_over_metadata_pdf_url(monkeypatch):
    record = {"detail_file_path": "decision.pdf", "file_url": "https://example.test/actual.pdf",
              "extracted": {"PDFURL": "https://example.test/listed.pdf"}}
    assert _persist_with_fakes(monkeypatch, record)["file_url"] == record["file_url"]


def _retry_pending(monkeypatch, items, returned_records, *, completed=True):
    from repositories import jobStateRepository
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    jobStateRepository.save_state("media", "DATA", {"items": {item["record_key"]: item for item in items}})
    crawl = MagicMock(return_value=SimpleNamespace(records=returned_records, completed_safely=completed))
    monkeypatch.setattr(module, "crawl", crawl)
    result = MediaAcquisitionService._acquire_todo_items(_crawler_config())
    return result, crawl


def test_existing_local_publication_is_reused_without_crawling(tmp_path, monkeypatch):
    path = tmp_path / "decision.pdf"
    path.write_bytes(b"%PDF-original")
    context = {"kind": "form_search", "record_key": "query"}
    item = {"kind": "process", "record_key": "publication", "detail_file_path": str(path),
            "extracted": {"Title": "Decision"}, "discovery_context": context}

    result, crawl = _retry_pending(monkeypatch, [item], [])

    crawl.assert_not_called()
    [record] = result.records
    assert record["extracted"] == item["extracted"]
    assert record["discovery_context"] == context
    assert result.completed_safely is True


def test_missing_local_publication_is_reacquired_through_saved_navigation(tmp_path, monkeypatch):
    context = {"kind": "form_search", "record_key": "query", "recipe": {"start_url": "https://example.test/search"}}
    missing = {"kind": "process", "record_key": "publication", "detail_file_path": str(tmp_path / "missing.pdf"),
               "discovery_context": context}
    replacement = {"record_key": "publication", "detail_file_path": "new.pdf"}

    result, crawl = _retry_pending(monkeypatch, [missing], [replacement])

    task = crawl.call_args.kwargs["task"]
    assert task.discovery_items == [context]
    assert task.detail_items == []
    assert result.records == [replacement]
    assert result.missing_detail_count == 0
    assert result.completed_safely is True


def test_query_and_existing_publication_do_not_emit_duplicate_records(tmp_path, monkeypatch):
    path = tmp_path / "decision.pdf"
    path.write_bytes(b"%PDF-original")
    query = {"kind": "form_search", "record_key": "query"}
    saved = {"kind": "process", "record_key": "publication", "detail_file_path": str(path), "discovery_context": query}
    rediscovered = {"record_key": "publication", "detail_file_path": "new.pdf", "extracted": {"Title": "Updated"}}

    result, _ = _retry_pending(monkeypatch, [query, saved], [rediscovered])

    assert result.records == [rediscovered]
    assert result.missing_detail_count == 0  # A query is not a publication.
    assert result.completed_safely is True


@pytest.mark.parametrize("records,completed,expected", [
    ([], True, True),  # An explicit empty-result search is complete.
    ([{"record_key": "query", "failed": True}], True, False),
    ([], False, False),  # An interrupted crawl must not become a successful retry.
])
def test_search_retry_completion_reflects_failures_and_crawl_status(monkeypatch, records, completed, expected):
    query = {"kind": "form_search", "record_key": "query"}
    result, _ = _retry_pending(monkeypatch, [query], records, completed=completed)
    assert result.completed_safely is expected


@pytest.mark.parametrize("mode,expected", [("INITIAL", "historical"), ("INCREMENTAL", "recent")])
def test_acquire_uses_mode_configuration_without_mutating_source(monkeypatch, mode, expected):
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    source = {
        **_crawler_config(),
        "discovery": {"policy": "full_scan", "form_search": "recent"},
        "acquisition": {"type": "crawler", "fetch_strategy": "direct"},
        "bypass_config": {"enabled": False},
        "discovery_by_mode": {
            "INITIAL": {
                "form_search": "historical",
                "acquisition": {"fetch_strategy": "saved_html"},
                "bypass_config": {"enabled": True},
            },
            "INCREMENTAL": {"form_search": "recent"},
        },
    }
    original = deepcopy(source)
    monkeypatch.setattr(MediaAcquisitionService, "_validate_source_config", lambda *_: None)
    monkeypatch.setattr(MediaAcquisitionService, "_resolve_lookup_values", lambda *_, **__: {"source_id": 1, "dataset_id": 2})
    acquire = MagicMock(return_value=module.MediaSourceAcquisitionResult())
    monkeypatch.setattr(MediaAcquisitionService, "_acquire_source", acquire)

    MediaAcquisitionService().acquire(source, mode)

    resolved = acquire.call_args.kwargs["source_config"]
    assert resolved["discovery"] == {"policy": "full_scan", "form_search": expected}
    assert resolved["acquisition"] == {
        "type": "crawler",
        "fetch_strategy": "saved_html" if mode == "INITIAL" else "direct",
    }
    assert resolved["bypass_config"] == {"enabled": mode == "INITIAL"}
    assert source == original


def test_acquire_preserves_configuration_without_mode_overrides(monkeypatch):
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    source = {**_crawler_config(), "discovery": {"policy": "full_scan"}}
    original = deepcopy(source)
    monkeypatch.setattr(MediaAcquisitionService, "_validate_source_config", lambda *_: None)
    monkeypatch.setattr(MediaAcquisitionService, "_resolve_lookup_values", lambda *_, **__: {"source_id": 1, "dataset_id": 2})
    acquire = MagicMock(return_value=module.MediaSourceAcquisitionResult())
    monkeypatch.setattr(MediaAcquisitionService, "_acquire_source", acquire)

    MediaAcquisitionService().acquire(source, "INITIAL")

    assert acquire.call_args.kwargs["source_config"] == original
    assert source == original


def test_persistence_failure_keeps_navigation_context_for_recovery(monkeypatch):
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    context = {"kind": "form_search", "record_key": "query"}
    record = {"record_key": "publication", "detail_file_path": "decision.pdf", "discovery_context": context,
              "retry_listing": True, "attachment_manifest": True, "discovery_retry_item": context}
    source = {**_crawler_config(), "discovery": {"policy": "full_scan"}}
    monkeypatch.setattr(MediaAcquisitionService, "_validate_source_config", lambda *_: None)
    monkeypatch.setattr(MediaAcquisitionService, "_resolve_lookup_values", lambda *_, **__: {"source_id": 1, "dataset_id": 2})
    monkeypatch.setattr(MediaAcquisitionService, "_acquire_source", lambda *_, **__: module.MediaSourceAcquisitionResult(records=[record]))
    monkeypatch.setattr(MediaAcquisitionService, "_persist_record", MagicMock(side_effect=OSError("Storage unavailable")))

    result = MediaAcquisitionService().acquire(source, "INITIAL")

    [failed] = result.records
    assert failed["failed"] is True
    assert failed["error_stage"] == "RAW_PERSISTENCE"
    assert failed["discovery_context"] == context
    assert failed["detail_file_path"] == "decision.pdf"
    assert failed["retry_listing"] is True
    assert failed["attachment_manifest"] is True
    assert failed["discovery_retry_item"] == context


def test_listing_retry_refreshes_index_instead_of_fetching_shared_url_as_detail(monkeypatch):
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    source = {**_crawler_config(), "discovery": {"strategy": "list_only", "policy": "full_scan"}}
    monkeypatch.setattr(MediaAcquisitionService, "_validate_source_config", lambda *_: None)
    monkeypatch.setattr(MediaAcquisitionService, "_resolve_lookup_values", lambda *_, **__: {"source_id": 1, "dataset_id": 2})
    monkeypatch.setattr(module.mediaTodoService, "load_items", lambda _: [{"kind": "list_only", "record_key": "entry"}])
    acquire = MagicMock(return_value=module.MediaSourceAcquisitionResult())
    todo_acquire = MagicMock()
    monkeypatch.setattr(MediaAcquisitionService, "_acquire_source", acquire)
    monkeypatch.setattr(MediaAcquisitionService, "_acquire_todo_items", todo_acquire)

    MediaAcquisitionService().acquire(source, "RETRY")

    todo_acquire.assert_not_called()
    acquire.assert_called_once()
    assert acquire.call_args.kwargs["mode"] == "INCREMENTAL"
    assert acquire.call_args.kwargs["discovery_policy"] == "full_scan"


def test_raw_persistence_logs_progress_every_100_records(monkeypatch, caplog):
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    records = [
        {"record_key": f"record-{index}", "detail_file_path": f"{index}.html"}
        for index in range(101)
    ]
    source = {**_crawler_config(), "discovery": {"policy": "full_scan"}}
    monkeypatch.setattr(MediaAcquisitionService, "_validate_source_config", lambda *_: None)
    monkeypatch.setattr(
        MediaAcquisitionService,
        "_resolve_lookup_values",
        lambda *_, **__: {"source_id": 1, "dataset_id": 2},
    )
    monkeypatch.setattr(
        MediaAcquisitionService,
        "_acquire_source",
        lambda *_, **__: module.MediaSourceAcquisitionResult(
            records=records,
            discovered_count=101,
            new_count=101,
            selected_detail_count=101,
            reached_source_end=True,
            completed_safely=True,
        ),
    )
    monkeypatch.setattr(
        MediaAcquisitionService,
        "_persist_record",
        MagicMock(side_effect=lambda **kwargs: {
            **kwargs["record"], "is_duplicate": False, "media_file_id": 1,
        }),
    )

    with caplog.at_level(logging.INFO):
        result = MediaAcquisitionService().acquire(source, "INITIAL")

    progress = [
        record.message for record in caplog.records
        if "Media Raw persistence progress" in record.message
    ]
    assert len(progress) == 2
    assert "processed=100 total=101 stored=100" in progress[0]
    assert "processed=101 total=101 stored=101" in progress[1]
    assert "remaining=0" in progress[1]
    assert result.stored_count == 101


def test_four_raw_workers_preserve_order_and_isolate_one_failure(monkeypatch):
    from services.adverseMediaPipeline import mediaAcquisitionService as module

    records = [
        {"record_key": f"record-{index}", "detail_file_path": f"{index}.html"}
        for index in range(8)
    ]
    source = {
        **_crawler_config(),
        "discovery": {"policy": "full_scan"},
        "acquisition": {"crawler_settings": {"raw_workers": 4}},
    }
    monkeypatch.setattr(MediaAcquisitionService, "_validate_source_config", lambda *_: None)
    monkeypatch.setattr(
        MediaAcquisitionService, "_resolve_lookup_values",
        lambda *_, **__: {"source_id": 1, "dataset_id": 2},
    )
    monkeypatch.setattr(
        MediaAcquisitionService, "_acquire_source",
        lambda *_, **__: module.MediaSourceAcquisitionResult(
            records=records, discovered_count=8, new_count=8,
            selected_detail_count=8, reached_source_end=True, completed_safely=True,
        ),
    )
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def persist(_self, **kwargs):
        nonlocal active, maximum_active
        key = kwargs["record"]["record_key"]
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.01)
            if key == "record-3":
                raise OSError("Seaweed unavailable")
            return {**kwargs["record"], "is_duplicate": False, "media_file_id": 1}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(MediaAcquisitionService, "_persist_record", persist)

    result = MediaAcquisitionService().acquire(source, "INITIAL")

    assert maximum_active == 4
    assert [record["record_key"] for record in result.records] == [
        f"record-{index}" for index in range(8)
    ]
    assert result.stored_count == 7
    assert result.failed_count == 1
    assert result.records[3]["failed"] is True
    assert result.records[3]["error_stage"] == "RAW_PERSISTENCE"
