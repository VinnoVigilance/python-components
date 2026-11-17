"""Prepare article analysis for the media post-processing repository."""

from intelligence.adverseStatus.predictor import DECISION_THRESHOLD, MODEL_SHA256, MODEL_VERSION
from infrastructure.database.connection import get_db_connection
from repositories.mediaPostProcess import mediaRecordPostProcessRepository as repository
from intelligence.summary.mediaSummarizer import Summarizer, VERSION as SUMMARIZER_VERSION
from utils.hashing import calculate_record_hash


class MediaPostProcessingService:
    """Combine the existing summarizer and a run-owned adverse-news predictor.

    The caller supplies bounded batches and owns the predictor's lifetime.
    Calculation happens outside database transactions. Processor errors
    propagate to the caller instead of becoming valid-looking results.
    """

    def __init__(self, predictor):
        self._predictor = predictor
        self._summarizer = Summarizer(engine="hybrid", sentences=0)

    def prepare_batch(self, records):
        """Prepare results in input order for core media records.

        Each record contains id, vv_media_id, version_no, media_payload, and
        an optional source_name for the processor manifest.
        """
        inputs = []
        summaries = []
        for record in records:
            # Check identifiers before doing any potentially expensive work.
            record["id"], record["vv_media_id"], record["version_no"]
            payload = record["media_payload"]
            if not isinstance(payload, dict):
                raise TypeError("media_payload must be a dictionary")
            content = payload.get("Content")
            if content is None:
                content = {}
            if not isinstance(content, dict):
                raise TypeError("media_payload.Content must be a dictionary")
            title = content.get("Title")
            body = content.get("BodyText")
            if title is not None and not isinstance(title, str):
                raise TypeError("Content.Title must be a string or None")
            if body is not None and not isinstance(body, str):
                raise TypeError("Content.BodyText must be a string or None")
            inputs.append({"title": title, "body": body})
            summary = self._summarizer.summarize(body, title=title or "")
            summaries.append((summary.status, summary.summary, summary.flags))
            # The result also contains cleaned article text and sentence spans.
            # Do not keep these large intermediate values for the entire batch.
            del summary

        predictions = self._predictor.predict_many(inputs)
        if len(predictions) != len(records):
            raise ValueError("Predictor returned a different number of results")

        prepared = []
        for record, (status, text, flags), prediction in zip(records, summaries, predictions):
            analysis = {
                "summary": {
                    "status": status,
                    "text": text if status == "AUTO_CANDIDATE" else None,
                },
                "adversePrediction": {
                    "status": prediction["status"],
                    "modelPositive": prediction["model_positive"],
                },
            }
            manifest = {
                "summarizer": {
                    "version": SUMMARIZER_VERSION,
                    "engine": self._summarizer.engine,
                    "sentences": self._summarizer.sentences,
                    "maxInputChars": self._summarizer.max_input_chars,
                    "flags": flags,
                },
                "adverseNews": {
                    "modelVersion": MODEL_VERSION,
                    "modelSha256": MODEL_SHA256,
                    "threshold": DECISION_THRESHOLD,
                    "score": prediction["score"],
                    "reviewRequired": prediction["review_required"],
                    "sourceName": record.get("source_name"),
                },
            }
            prepared.append({
                "media_record_id": record["id"],
                "vv_media_id": record["vv_media_id"],
                "media_version": record["version_no"],
                "analysis_payload": analysis,
                "processor_manifest": manifest,
                "post_process_hash": calculate_record_hash(analysis),
            })
        return prepared

    @staticmethod
    def expire_deleted_article(record):
        """Expire the active result for a DELETE delta without running processors."""
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                if not repository.lock_delete_source(cursor, record):
                    return "stale"
                current = repository.find_current_result(cursor, record["vv_media_id"])
                if current is None:
                    return "unchanged"
                if current[2] > record["version_no"]:
                    return "stale"
                repository.expire_result(cursor, current[0])
                return "deleted"

    def save_result(self, record):
        """Atomically save one prepared result if its current source still matches.

        The caller must handle STALE by selecting current work on a later scan.
        A transaction error is raised and rolls back any expiry and insertion.
        """
        if calculate_record_hash(record["analysis_payload"]) != record["post_process_hash"]:
            raise ValueError("Prepared post-processing payload does not match its hash")

        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                if not repository.lock_current_source(cursor, record):
                    return {"action": "STALE", "id": None}

                current = repository.find_current_result(cursor, record["vv_media_id"])
                if current and current[1] == record["post_process_hash"]:
                    return {"action": "UNCHANGED", "id": current[0]}

                if current:
                    repository.expire_result(cursor, current[0])
                result_id = repository.insert_result(cursor, record)
                return {"action": "UPDATED" if current else "INSERTED", "id": result_id}
