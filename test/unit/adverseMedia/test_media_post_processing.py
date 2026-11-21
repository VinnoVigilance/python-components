"""Exercise article post-processing orchestration and result contracts."""

from contextlib import nullcontext
from datetime import date, timedelta
from unittest.mock import MagicMock, Mock, call

import pytest

from pipelines import mediaPostProcessingPipeline as pipeline
from repositories.mediaPostProcess import mediaRecordPostProcessRepository as result_repository
from services.adverseMediaPostProcessPipeline import mediaPostProcessingService as service
from utils.hashing import calculate_record_hash


@pytest.mark.parametrize(("mode", "outcome"), [
    ("initial", "INSERTED"),
    ("reprocess", "UNCHANGED"),
])
def test_core_modes_reuse_one_predictor_across_pages(monkeypatch, mode, outcome):
    connection = object()
    monkeypatch.setattr(pipeline, "get_db_connection", lambda: nullcontext(connection))
    rows = [{"id": article_id, "media_payload": {}} for article_id in (1, 2)]
    repository = Mock()
    repository.scan_end.return_value = 2
    repository.find_current_batch.side_effect = [[rows[0]], [rows[1]], []]
    monkeypatch.setattr(pipeline, "repository", repository)
    predictor = Mock()
    monkeypatch.setattr(pipeline, "AdverseNewsPredictor", predictor)
    processor = Mock()
    processor.prepare_batch.return_value = [
        {"analysis_payload": {"adversePrediction": {"status": "SCORED"}}},
    ]
    processor.save_result.return_value = {"action": outcome}
    monkeypatch.setattr(pipeline, "MediaPostProcessingService", Mock(return_value=processor))

    result = pipeline.run_media_post_processing(mode, batch_size=1)

    predictor.assert_called_once_with()
    repository.scan_end.assert_called_once_with(connection, incremental=False)
    assert repository.find_current_batch.call_args_list == [
        call(connection, after_id, 2, 1) for after_id in (0, 1, 2)
    ]
    assert processor.prepare_batch.call_args_list == [call([row]) for row in rows]
    assert result["counts"]["scored"] == result["counts"][outcome.lower()] == 2


def test_incremental_scans_dates_before_ids_and_expires_delete(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr(pipeline, "get_db_connection", lambda: nullcontext(connection))
    first = date(2026, 11, 13)
    dates = [first + timedelta(days=offset) for offset in range(4)]
    monkeypatch.setattr(pipeline, "destination_delta_window", lambda *_: (dates[0], dates[-1]))
    add, update, delete = [
        dict(action=action, delta_id=delta_id, id=delta_id, vv_media_id=delta_id,
             delta_vv_media_id=delta_id, media_payload={}, is_current=action != "DELETE",
             change_type=None)
        for action, delta_id in (("ADD", 30), ("UPDATE", 20), ("DELETE", 10))
    ]
    repository = Mock()
    repository.scan_end.return_value = 30
    repository.find_delta_batch.side_effect = [[add], [], [], [update], [], [delete], []]
    monkeypatch.setattr(pipeline, "repository", repository)
    monkeypatch.setattr(pipeline, "AdverseNewsPredictor", Mock())
    processor = Mock()
    processor.prepare_batch.return_value = [
        {"analysis_payload": {"adversePrediction": {"status": "SCORED"}}},
    ]
    processor.save_result.side_effect = [{"action": "INSERTED"}, {"action": "UPDATED"}]
    factory = Mock(return_value=processor)
    factory.expire_deleted_article.return_value = "deleted"
    monkeypatch.setattr(pipeline, "MediaPostProcessingService", factory)

    result = pipeline.run_media_post_processing("incremental", batch_size=1)

    assert repository.find_delta_batch.call_args_list == [
        call(connection, day, after_id, 30, 1)
        for day, after_id in [
            (dates[0], 0), (dates[0], 30), (dates[1], 0), (dates[2], 0),
            (dates[2], 20), (dates[3], 0), (dates[3], 10),
        ]
    ]
    assert processor.prepare_batch.call_args_list == [call([add]), call([update])]
    factory.expire_deleted_article.assert_called_once_with(delete)
    assert result["counts"]["scanned"] == 3
    assert all(result["counts"][key] == 1 for key in ("inserted", "updated", "deleted"))


def test_prepared_payload_hash_excludes_score_and_review_metadata():
    predictor = Mock()
    predictor.predict_many.side_effect = [
        [{"status": "SCORED", "model_positive": True, "score": score, "review_required": True}]
        for score in (0.8, 1.2)
    ]
    processor = service.MediaPostProcessingService(predictor)
    article = {
        "id": 9008,
        "vv_media_id": 410,
        "version_no": 2,
        "media_payload": {"Content": {"Title": "Headline", "BodyText": "Article body."}},
    }

    [first] = processor.prepare_batch([article])
    [second] = processor.prepare_batch([article])

    assert first["analysis_payload"]["adversePrediction"] == {
        "status": "SCORED", "modelPositive": True,
    }
    manifest = first["processor_manifest"]["adverseNews"]
    assert manifest["score"] == 0.8
    assert manifest["reviewRequired"] is True
    assert manifest["sourceName"] is None
    assert second["processor_manifest"]["adverseNews"]["score"] == 1.2
    assert first["post_process_hash"] == second["post_process_hash"]
    assert first["post_process_hash"] == calculate_record_hash(first["analysis_payload"])
    predictor.predict_many.assert_called_with([{"title": "Headline", "body": "Article body."}])


@pytest.mark.parametrize(("existing_hash", "action"), [
    (None, "INSERTED"),
    ("same", "UNCHANGED"),
    ("different", "UPDATED"),
])
def test_save_result_writes_only_when_payload_changes(monkeypatch, existing_hash, action):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(service, "get_db_connection", lambda: nullcontext(connection))
    payload = {"summary": {"status": "AUTO_CANDIDATE", "text": "Example"}}
    payload_hash = calculate_record_hash(payload)
    record = {"vv_media_id": 410, "analysis_payload": payload, "post_process_hash": payload_hash}
    repository = Mock()
    repository.lock_current_source.return_value = True
    repository.find_current_result.return_value = (
        None if existing_hash is None else
        (271, payload_hash if existing_hash == "same" else existing_hash, 1)
    )
    repository.insert_result.return_value = 272
    monkeypatch.setattr(service, "repository", repository)

    result = service.MediaPostProcessingService(Mock()).save_result(record)

    expected = [
        call.lock_current_source(cursor, record),
        call.find_current_result(cursor, 410),
    ]
    if action == "UPDATED":
        expected.append(call.expire_result(cursor, 271))
    if action != "UNCHANGED":
        expected.append(call.insert_result(cursor, record))
    assert repository.mock_calls == expected
    assert result == {"action": action, "id": 271 if action == "UNCHANGED" else 272}


def test_expiry_closes_both_date_and_current_flag():
    """The ALTER makes the flag writable; changing to_date no longer updates it."""
    cursor = Mock(rowcount=1)

    result_repository.expire_result(cursor, 271)

    query, parameters = cursor.execute.call_args.args
    assignments = query.upper().split("SET", 1)[1].split("WHERE", 1)[0]
    assert "IS_CURRENT = FALSE" in assignments
    assert "TO_DATE = NOW()" in assignments
    assert parameters == (271,)


def test_expiry_rejects_missing_or_already_expired_result():
    with pytest.raises(RuntimeError, match="Could not expire"):
        result_repository.expire_result(Mock(rowcount=0), 271)


def test_insert_explicitly_opens_current_result():
    cursor = Mock()
    cursor.fetchone.return_value = (272,)
    record = {
        "media_record_id": 9008, "vv_media_id": 410, "media_version": 2,
        "analysis_payload": {}, "processor_manifest": {}, "post_process_hash": "hash",
    }

    assert result_repository.insert_result(cursor, record) == 272

    query, parameters = cursor.execute.call_args.args
    columns, values = query.upper().split("VALUES", 1)
    assert "IS_CURRENT" in columns
    assert "TO_DATE" in columns
    assert "NOW(), NULL, TRUE" in values
    assert parameters[:3] == (9008, 410, 2)


def test_delete_expires_once_and_replay_preserves_history(monkeypatch):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(service, "get_db_connection", lambda: nullcontext(connection))
    repository = Mock()
    repository.lock_delete_source.return_value = True
    repository.find_current_result.side_effect = [(271, "hash", 1), None]
    monkeypatch.setattr(service, "repository", repository)
    record = {"id": 9008, "vv_media_id": 410, "version_no": 2}

    assert service.MediaPostProcessingService.expire_deleted_article(record) == "deleted"
    assert service.MediaPostProcessingService.expire_deleted_article(record) == "unchanged"
    repository.expire_result.assert_called_once_with(cursor, 271)
