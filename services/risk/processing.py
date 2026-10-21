"""Shared bounded processing loop for the two explicit risk repositories."""

import logging
from datetime import date, timedelta

from infrastructure.database.connection import connection_pool
from infrastructure.database.jobLock import advisory_job_lock
from intelligence.risk.interface import create_classifier
from intelligence.risk.result import build_assessment_payload
from repositories import jobStateRepository
from utils.hashing import calculate_record_hash


logger = logging.getLogger(__name__)


class ClassifierLoadError(RuntimeError):
    """The selected classifier cannot initialize for this run."""


def _process(connection, repository, item, mode, classify):
    """Persist one complete assessment or expiry in one transaction."""
    action = str(item.get("action", "UPDATE")).upper()
    if action not in {"ADD", "UPDATE", "DELETE"}:
        raise ValueError(f"Unsupported delta action: {action}.")
    source = (repository.find_source_version(connection, item["source_record_id"])
              if mode == "incremental" else repository.find_current_source(connection, item["subject_id"]))
    if source is None or source["subject_id"] != item["subject_id"]:
        raise LookupError(f"Missing matching source version for {item['subject_id']}.")
    latest = repository.find_current_source(connection, item["subject_id"]) if mode == "incremental" else source
    historical = latest is None or latest["id"] != source["id"]
    with connection.cursor() as cursor:
        if action == "DELETE" or source["change_type"] == "DELETED":
            current_risk = repository.find_current_risk(cursor, item["subject_id"])
            if (not historical and current_risk
                    and current_risk["version_no"] <= source["version_no"]):
                repository.expire_current(cursor, item["subject_id"])
            return "deleted"
        details = build_assessment_payload(classify(source["payload"] or {}))
        if details.get("assessmentStatus") not in {"CLASSIFIED", "SKIPPED_EMPTY_BODY"}:
            raise ValueError("Classifier did not return a completed or explicitly skipped assessment.")
        result_hash = calculate_record_hash(details)
        if historical:
            if repository.has_assessment(cursor, item["subject_id"], source["id"], result_hash):
                return "skipped"
            repository.insert_risk(cursor, source, details, result_hash,
                                   current=False)
            return "historical"
        current_risk = repository.find_current_risk(cursor, item["subject_id"])
        if (current_risk and current_risk["risk_details_hash"] == result_hash
                and current_risk["version_no"] == source["version_no"]
                and current_risk["source_record_id"] == source["id"]):
            return "skipped"
        repository.expire_current(cursor, item["subject_id"])
        repository.insert_risk(cursor, source, details, result_hash)
        return "versioned"


def _attempt(connection, repository, subject, item, mode, effective_date, classify):
    """Try one record three times, preserving the prior assessment on failure."""
    for attempt in range(1, 4):
        try:
            with connection:
                outcome = _process(connection, repository, item, mode, classify)
            return outcome, None
        except ClassifierLoadError:
            raise
        except Exception as error:
            connection.rollback()
            logger.exception(
                "Risk record attempt failed: subject=%s date=%s logical_id=%s "
                "work_id=%s source_record_id=%s attempt=%s/3 exhausted=%s",
                subject, effective_date, item["subject_id"], item["id"],
                item.get("source_record_id", item["id"]), attempt, attempt == 3,
            )
            if attempt == 3:
                return None, error


def _scan(connection, repository, subject, mode, effective_date, source_name,
          batch_size, max_rows, classify, on_result=None):
    """Process one complete source scan or one effective date."""
    result = {"effective_date": effective_date, "processed_count": 0,
              "versioned_count": 0, "historical_count": 0, "skipped_count": 0,
              "deleted_count": 0, "failed_count": 0, "run_status": "SUCCESS",
              "limit_reached": False}
    with connection:
        with connection.cursor() as cursor:
            end_id = repository.find_scan_end(cursor, mode, effective_date)
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
            outcome, error = _attempt(connection, repository, subject, item, mode,
                                      effective_date, classify)
            if on_result is not None:
                on_result(item, mode, effective_date, error)
            if error is not None:
                result["failed_count"] += 1
            else:
                result[outcome + "_count"] += 1
            result["processed_count"] += 1
        after_id = items[-1]["id"]
        if max_rows and result["processed_count"] >= max_rows:
            with connection:
                if mode == "incremental":
                    remaining = repository.find_delta_batch(connection, effective_date, after_id, end_id, 1)
                else:
                    remaining = repository.find_current_batch(connection, after_id, end_id, 1, source_name)
            result["limit_reached"] = bool(remaining)
            if remaining:
                result["run_status"] = "LIMIT_REACHED"
            break
    if result["failed_count"]:
        result["run_status"] = "PARTIAL" if result["failed_count"] < result["processed_count"] else "FAILED"
    return result


def run(subject, repository, lock_id, mode="incremental", effective_date=None,
        source_name=None, model_path=None, batch_size=1000, max_rows=None,
        threads=4, classifier=None):
    """Retry failed work IDs, then scan current records or new delta dates."""
    mode = mode.lower()
    if mode not in {"initial", "incremental"}:
        raise ValueError("mode must be initial or incremental.")
    if batch_size < 1 or (max_rows is not None and max_rows < 1):
        raise ValueError("batch_size and max_rows must be positive.")
    if mode != "incremental" and effective_date is not None:
        raise ValueError("effective_date applies only to incremental processing.")
    if mode == "incremental" and source_name is not None:
        raise ValueError("Incremental scope is the selected delta batch; omit source_name.")
    result = {"subject": subject, "mode": mode, "effective_date": effective_date,
              "processed_count": 0, "versioned_count": 0, "historical_count": 0,
              "skipped_count": 0, "deleted_count": 0, "failed_count": 0,
              "run_status": "SUCCESS", "limit_reached": False,
              "last_scanned_date": None, "target_date": None, "pending_count": 0,
              "retried_count": 0, "dates": []}

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
            state = jobStateRepository.load_state("risk", subject) or {}
            pending_items = state.setdefault("items", {})
            target_date = effective_date if effective_date is not None else date.today()

            def record_result(item, item_mode, item_date, error):
                key = f"{item_mode}:{item['id']}"
                previous = pending_items.get(key)
                if error is not None:
                    pending_items[key] = {
                        "item": {key: item[key] for key in
                                 ("id", "subject_id", "source_record_id", "action") if key in item},
                        "mode": item_mode,
                        "effective_date": item_date.isoformat() if item_date else None,
                        "source_name": (previous or {}).get("source_name", source_name) if item_mode == "initial" else None,
                        "attempts": (previous or {}).get("attempts", 0) + 3,
                        "error": str(error), "error_type": type(error).__name__,
                    }
                elif previous is not None:
                    del pending_items[key]
                else:
                    return
                jobStateRepository.save_state("risk", subject, state)

            queued = sorted(list(pending_items.values()),
                            key=lambda entry: (entry.get("effective_date") or "", entry["item"]["id"]))
            for entry in queued:
                item_date = date.fromisoformat(entry["effective_date"]) if entry.get("effective_date") else None
                if item_date and item_date > target_date:
                    continue
                if mode == "initial" and source_name and entry.get("source_name") != source_name:
                    continue
                if max_rows and result["processed_count"] >= max_rows:
                    result["limit_reached"] = True
                    break
                outcome, error = _attempt(connection, repository, subject, entry["item"],
                                          entry["mode"], item_date, classify)
                record_result(entry["item"], entry["mode"], item_date, error)
                result["processed_count"] += 1
                result["retried_count"] += 1
                if error is not None:
                    result["failed_count"] += 1
                else:
                    result[outcome + "_count"] += 1

            if mode == "incremental":
                with connection:
                    with connection.cursor() as cursor:
                        baseline = repository.latest_risk_date(cursor)
                result["target_date"] = target_date
                frontier = date.fromisoformat(state["next_date"]) if state.get("next_date") else baseline
                starts = [frontier] if frontier else []
                for key in ("inflight_date", "replay_from"):
                    if state.get(key):
                        starts.append(date.fromisoformat(state[key]))
                next_date = min(starts) if starts else None
                if next_date is None:
                    result["run_status"] = "INITIAL_REQUIRED"
                    result["no_work_reason"] = "No existing risk assessments; run INITIAL before incremental."
                last_available = target_date
            else:
                next_date = None
                last_available = None

            while not result["limit_reached"] and (mode == "initial" or (
                    next_date is not None and last_available is not None
                    and next_date <= last_available)):
                if max_rows and result["processed_count"] >= max_rows:
                    result["limit_reached"] = True
                    break
                logger.info("Risk scan: subject=%s mode=%s effective_date=%s",
                            subject, mode, next_date)
                if mode == "incremental":
                    state["inflight_date"] = next_date.isoformat()
                    if state.get("replay_from"):
                        state["next_date"] = next_date.isoformat()
                        state.pop("replay_from")
                    jobStateRepository.save_state("risk", subject, state)
                day = _scan(connection, repository, subject, mode, next_date, source_name,
                            batch_size,
                            max_rows - result["processed_count"] if max_rows else None,
                            classify, on_result=record_result)
                result["dates"].append(day)
                result["effective_date"] = next_date
                for key in ("processed_count", "versioned_count", "historical_count",
                            "skipped_count", "deleted_count", "failed_count"):
                    result[key] += day[key]
                result["limit_reached"] = day["limit_reached"]
                if mode == "initial":
                    break
                result["last_scanned_date"] = next_date
                if day["limit_reached"]:
                    break
                state["next_date"] = max(state.get("next_date", next_date.isoformat()),
                                         (next_date + timedelta(days=1)).isoformat())
                state.pop("inflight_date", None)
                jobStateRepository.save_state("risk", subject, state)
                next_date += timedelta(days=1)

            result["pending_count"] = len(pending_items)
            if result["limit_reached"]:
                result["run_status"] = "LIMIT_REACHED"
            elif result["failed_count"]:
                result["run_status"] = ("FAILED" if result["failed_count"] == result["processed_count"] else "PARTIAL")
            elif pending_items:
                result["run_status"] = "PARTIAL"
        finally:
            connection_pool.putconn(connection)
    return result
