"""Shared bounded processing loop for the two explicit risk repositories."""

import logging

from infrastructure.database.connection import connection_pool
from infrastructure.database.jobLock import advisory_job_lock
from intelligence.risk.interface import create_classifier
from intelligence.risk.result import build_assessment_payload
from utils.hashing import calculate_record_hash


logger = logging.getLogger(__name__)


class ClassifierLoadError(RuntimeError):
    """The selected classifier cannot initialize for this run."""


def _process(connection, repository, item, mode, classify):
    """Persist one complete assessment or expiry in one transaction."""
    action = str(item.get("action", "UPDATE")).upper()
    if action not in {"ADD", "UPDATE", "DELETE"}:
        raise ValueError(f"Unsupported delta action: {action}.")
    current = repository.find_current_source(connection, item["subject_id"])
    expected_id = item["source_record_id"] if mode == "incremental" else item["id"]
    with connection.cursor() as cursor:
        if action == "DELETE":
            if current and current["change_type"] != "DELETED" and current["id"] != expected_id:
                return "superseded"
            repository.expire_current(cursor, item["subject_id"])
            return "deleted"
        if current is None:
            raise LookupError(f"Missing current source for {item['subject_id']}.")
        if current["id"] != expected_id:
            return "superseded"
        if current["change_type"] == "DELETED":
            repository.expire_current(cursor, item["subject_id"])
            return "deleted"
        details = build_assessment_payload(classify(current["payload"] or {}))
        if details.get("assessmentStatus") not in {"CLASSIFIED", "SKIPPED_EMPTY_BODY"}:
            raise ValueError("Classifier did not return a completed or explicitly skipped assessment.")
        result_hash = calculate_record_hash(details)
        current_risk = repository.find_current_risk(cursor, item["subject_id"])
        if (current_risk and current_risk["risk_details_hash"] == result_hash
                and current_risk["version_no"] == current["version_no"]):
            return "skipped"
        repository.expire_current(cursor, item["subject_id"])
        repository.insert_risk(cursor, current, details, result_hash)
        return "versioned"


def run(subject, repository, lock_id, mode="incremental", effective_date=None,
        source_name=None, model_path=None, batch_size=1000, max_rows=None,
        threads=4, classifier=None):
    """Run initial, incremental or explicit risk-only reprocessing."""
    mode = mode.lower()
    if mode not in {"initial", "incremental", "reprocess"}:
        raise ValueError("mode must be initial, incremental or reprocess.")
    if batch_size < 1 or (max_rows is not None and max_rows < 1):
        raise ValueError("batch_size and max_rows must be positive.")
    if mode != "incremental" and effective_date is not None:
        raise ValueError("effective_date applies only to incremental processing.")
    if mode == "incremental" and effective_date is None:
        raise ValueError("incremental processing requires the selected effective_date.")
    if mode == "incremental" and source_name is not None:
        raise ValueError("Incremental scope is the selected delta batch; omit source_name.")
    result = {"subject": subject, "mode": mode, "effective_date": effective_date,
              "processed_count": 0, "versioned_count": 0, "skipped_count": 0,
              "deleted_count": 0, "superseded_count": 0, "failed_count": 0,
              "run_status": "SUCCESS", "limit_reached": False}

    def classify(payload):
        nonlocal classifier
        if classifier is None:
            try:
                classifier = create_classifier(subject, model_path, threads)
            except Exception as error:
                raise ClassifierLoadError(f"Cannot initialize {subject} classifier: {error}") from error
        return classifier.classify(payload)

    with advisory_job_lock(lock_id) as acquired:
        if not acquired:
            raise RuntimeError(f"Another {subject} risk job is already running.")
        connection = connection_pool.getconn()
        try:
            with connection:
                with connection.cursor() as cursor:
                    end_id = repository.find_scan_end(cursor, mode, effective_date)
            result["effective_date"] = effective_date
            after_id = 0
            while after_id < end_id:
                size = min(batch_size, max_rows - result["processed_count"]) if max_rows else batch_size
                with connection:
                    if mode == "incremental":
                        items = repository.find_delta_batch(connection, effective_date, after_id, end_id, size)
                    else:
                        items = repository.find_current_batch(connection, after_id, end_id, size, source_name)
                if not items:
                    break
                for item in items:
                    try:
                        with connection:
                            outcome = _process(connection, repository, item, mode, classify)
                        result[outcome + "_count"] += 1
                    except ClassifierLoadError:
                        raise
                    except Exception:
                        connection.rollback()
                        result["failed_count"] += 1
                        logger.exception("Risk processing failed: subject=%s logical_id=%s work_id=%s",
                                         subject, item["subject_id"], item["id"])
                    result["processed_count"] += 1
                after_id = items[-1]["id"]
                if max_rows and result["processed_count"] >= max_rows:
                    with connection:
                        if mode == "incremental":
                            remaining = repository.find_delta_batch(
                                connection, effective_date, after_id, end_id, 1,
                            )
                        else:
                            remaining = repository.find_current_batch(
                                connection, after_id, end_id, 1, source_name,
                            )
                    result["limit_reached"] = bool(remaining)
                    if remaining:
                        result["run_status"] = "LIMIT_REACHED"
                    break
        finally:
            connection_pool.putconn(connection)
    if result["failed_count"]:
        result["run_status"] = "PARTIAL" if result["failed_count"] < result["processed_count"] else "FAILED"
    return result
