"""Run article post-processing against the deployed media tables."""

import logging
from itertools import groupby
from time import perf_counter

from infrastructure.database.connection import get_db_connection
from intelligence.adverseStatus.predictor import AdverseNewsPredictor
from repositories.mediaPostProcess import mediaRecordPostProcessRepository as repository
from services.adverseMediaPipeline.mediaPostProcessingService import MediaPostProcessingService
from services.common.incrementalDeltaScan import destination_delta_window, iter_effective_dates


logger = logging.getLogger(__name__)


def _text_batches(records, limit):
    """Keep full article text and isolate articles larger than the batch limit."""
    batch = []
    size = 0
    for record in records:
        content = record["media_payload"]
        content = content.get("Content") if isinstance(content, dict) else None
        length = 0
        if isinstance(content, dict):
            length = sum(
                len(content[key]) for key in ("Title", "BodyText")
                if isinstance(content.get(key), str)
            )
        if batch and size + length > limit:
            yield batch
            batch, size = [], 0
        batch.append(record)
        size += length
    if batch:
        yield batch


def run_media_post_processing(mode, *, batch_size=100, max_batch_chars=1_000_000):
    """Process a bounded ID scan and return counts and elapsed seconds."""
    if mode not in {"initial", "incremental", "reprocess"}:
        raise ValueError("mode must be initial, incremental, or reprocess")
    for value in (batch_size, max_batch_chars):
        if type(value) is not int or value < 1:
            raise ValueError("batch_size and max_batch_chars must be positive integers")

    incremental = mode == "incremental"
    id_key = "delta_id" if incremental else "id"
    counts = dict.fromkeys((
        "scanned", "scored", "inserted", "updated", "unchanged", "stale",
        "deleted", "unexpected_action", "invalid_reference",
    ), 0)
    seconds = dict.fromkeys(("read", "model_load", "prepare", "write"), 0.0)
    started = perf_counter()
    service = None

    try:
        stage = perf_counter()
        with get_db_connection() as connection:
            start_date = end_date = None
            if incremental:
                with connection.cursor() as cursor:
                    start_date, end_date = destination_delta_window(
                        cursor, repository.latest_result_date,
                    )
            end_id = repository.scan_end(connection, incremental=incremental)
        seconds["read"] += perf_counter() - stage

        after_id = 0
        effective_dates = iter_effective_dates(start_date, end_date)
        effective_date = next(effective_dates, None)
        initial_required = incremental and start_date is None
        while end_id and (not incremental or effective_date is not None):
            stage = perf_counter()
            with get_db_connection() as connection:
                if incremental:
                    rows = repository.find_delta_batch(
                        connection, effective_date, after_id, end_id, batch_size,
                    )
                else:
                    rows = repository.find_current_batch(connection, after_id, end_id, batch_size)
            seconds["read"] += perf_counter() - stage
            if not rows:
                if incremental:
                    effective_date = next(effective_dates, None)
                    after_id = 0
                    continue
                break

            after_id = rows[-1][id_key]
            counts["scanned"] += len(rows)
            candidates = []
            for row in rows:
                if incremental:
                    if row["action"] not in {"ADD", "UPDATE", "DELETE"}:
                        counts["unexpected_action"] += 1
                        logger.warning("Skipping delta %s (%s) for article %s: action %s",
                                       row["delta_id"], row["effective_date"],
                                       row["delta_vv_media_id"], row["action"])
                        continue
                    if row["id"] is None or row["vv_media_id"] != row["delta_vv_media_id"]:
                        counts["invalid_reference"] += 1
                        logger.warning("Invalid core reference %s in delta %s",
                                       row["delta_media_record_id"], row["delta_id"])
                        continue
                    if row["action"] != "DELETE" and (
                        not row["is_current"] or (row["change_type"] or "").upper() == "DELETED"
                    ):
                        counts["stale"] += 1
                        continue
                candidates.append(row)

            for deleting, group in groupby(candidates, key=lambda row: row.get("action") == "DELETE"):
                if deleting:
                    stage = perf_counter()
                    for row in group:
                        outcome = MediaPostProcessingService.expire_deleted_article(row)
                        counts[outcome] += 1
                    seconds["write"] += perf_counter() - stage
                    continue
                for batch in _text_batches(group, max_batch_chars):
                    if service is None:
                        stage = perf_counter()
                        service = MediaPostProcessingService(AdverseNewsPredictor())
                        seconds["model_load"] += perf_counter() - stage
                    stage = perf_counter()
                    prepared = service.prepare_batch(batch)
                    seconds["prepare"] += perf_counter() - stage
                    stage = perf_counter()
                    for result in prepared:
                        status = result["analysis_payload"]["adversePrediction"]["status"]
                        counts["scored"] += status == "SCORED"
                        outcome = service.save_result(result)
                        counts[outcome["action"].lower()] += 1
                    seconds["write"] += perf_counter() - stage
    finally:
        # The service holds the run's only reference to the predictor.
        service = None

    elapsed = perf_counter() - started
    return {
        "mode": mode,
        "status": "INITIAL_REQUIRED" if initial_required else "COMPLETED",
        "start_date": start_date,
        "end_date": end_date,
        "scan_end_id": end_id,
        "counts": counts,
        "seconds": {**seconds, "total": elapsed},
        "scored_per_second": counts["scored"] / elapsed if elapsed else 0.0,
    }
