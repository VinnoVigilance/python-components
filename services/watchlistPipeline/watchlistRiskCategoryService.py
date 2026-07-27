"""
Member Risk Category ETL - calculation & versioning service.

Implements the guideline "ETL Developer Guide - Member Risk Category Calculation
& Versioning". The Risk Category Calculation Engine (risk/riskEngine.py) turns a
member's ``full_payload`` into a ``risk_details`` document; this service hashes it
and maintains the SCD Type 2 history in ``core.member_risk_category``.

Two execution modes
-------------------
* ``run_initial_load``      - first-ever population. Ignores the delta table and
                              processes every ``is_current = TRUE`` member using
                              the ADD workflow.
* ``run_incremental``       - every subsequent run. Processes the ADD / UPDATE /
                              DELETE actions of one delta ``effective_date``.

Versioning rules (guideline "Versioning Rules" table)
-----------------------------------------------------
    no risk category (empty result)      -> store nothing (see below)
    ADD/UPDATE + identical active hash  -> skip (no change)
    ADD/UPDATE + different/absent hash   -> expire current (if any) + insert new
    DELETE                               -> expire all active rows, no insert

Empty classifications
---------------------
A member whose calculation yields no risk category is never written. This covers
both a list the ListScope config marks as no-risk (e.g. DNFBP - the engine simply
returns no labels for it) and an included-list record that could not be
classified. Because an included list always contributes its provenance base label
(Layer 1), an included member never yields an empty result, so skipping the empty
case cannot leave a stale active row behind.

Change detection is driven ENTIRELY by ``risk_details_hash`` (canonical SHA-256
of ``risk_details``). ADD and UPDATE collapse to the same operation - compare the
freshly-calculated hash to the current active row, skip if equal, otherwise
version - so they share one code path (``_apply_member``).

Transaction model
------------------
The unit of atomicity is ONE member's *expire + insert*, committed together.
Commits happen every ``COMMIT_EVERY`` members (never mid-member), so a crash
leaves completed members durable and correct, never a half-updated one. Because
detection is hash-based, re-running the ETL skips already-processed members and
resumes where it stopped - the whole ETL is idempotent. This is why the huge
Initial Load is not wrapped in a single transaction.

One-active-row invariant
-------------------------
Before every insert the current active row is expired, so exactly one
``is_current = TRUE`` row can ever exist per ``vv_member_id`` - including the ADD
case where an active row unexpectedly already exists.
"""

import logging
from time import perf_counter

from infrastructure.database.connection import connection_pool
from repositories import memberRiskCategoryRepository as risk_repo
from risk.riskEngine import RiskEngine
from utils.hashing import calculate_record_hash


logger = logging.getLogger(__name__)

BATCH_SIZE = 1000       # members read per keyset page (Initial Load)
COMMIT_EVERY = 500      # members processed per commit (never splits a member)


# ---------------------------------------------------------------------------
# Per-member operations
# ---------------------------------------------------------------------------

def _apply_member(
    cursor,
    engine: RiskEngine,
    vv_member_id,
    watchlist_member_id: int,
    version_no: int,
    full_payload: dict,
) -> str:
    """ADD / UPDATE / Initial-Load logic for one member.

    Returns:
        "empty"     - the member has no risk category, so nothing is stored;
        "skipped"   - hash unchanged, already up to date;
        "versioned" - expired the old active row (if any) and inserted a new one.

    Guarantees the one-active-row invariant by expiring any current row before
    inserting.
    """
    risk_details = engine.classify(full_payload)

    # No risk category -> store nothing. Two ways a member lands here, both
    # intentionally treated the same: (1) its list is marked no-risk in the
    # ListScope config (e.g. DNFBP), so the engine returns no labels; (2) its
    # list DOES carry a category but this particular record could not be
    # classified. We never write an empty risk classification.
    #
    # For an included list the provenance base label (Layer 1) is always present,
    # so an included member does not reach here - meaning we never leave a stale
    # active row behind by skipping.
    if not (risk_details.get("RiskCategories") or []):
        return "empty"

    new_hash = calculate_record_hash(risk_details)

    current = risk_repo.find_current_risk(cursor, vv_member_id)

    if current is not None and current["risk_details_hash"] == new_hash:
        # Identical classification already active -> nothing to do. This is also
        # what makes a re-run idempotent (already-processed members are skipped).
        return "skipped"

    if current is not None:
        risk_repo.expire_current_risk(cursor, vv_member_id)

    risk_repo.insert_risk(
        cursor,
        {
            "vv_member_id": vv_member_id,
            "watchlist_member_id": watchlist_member_id,
            "version_no": version_no,
            "risk_details": risk_details,
            "risk_details_hash": new_hash,
        },
    )

    return "versioned"


def _delete_member(cursor, vv_member_id) -> bool:
    """DELETE logic: expire all active risk rows, insert nothing.

    Returns True if at least one active row was expired.
    """
    expired = risk_repo.expire_current_risk(cursor, vv_member_id)
    return expired > 0


# ---------------------------------------------------------------------------
# Initial Load
# ---------------------------------------------------------------------------

def run_initial_load(
    engine: RiskEngine | None = None,
    batch_size: int = BATCH_SIZE,
    commit_every: int = COMMIT_EVERY,
) -> dict[str, int]:
    """Populate risk categories for every current watchlist member.

    Ignores the delta table (guideline: Initial Load). Safe to re-run - hash
    detection skips members already classified.
    """
    started_at = perf_counter()
    engine = engine or RiskEngine()

    processed = 0
    versioned = 0
    skipped = 0
    empty = 0
    since_commit = 0
    last_member_id = 0

    connection = connection_pool.getconn()

    try:
        connection.autocommit = False
        cursor = connection.cursor()

        try:
            while True:
                members = risk_repo.find_current_members_batch(
                    cursor=cursor,
                    last_member_id=last_member_id,
                    batch_size=batch_size,
                )

                if not members:
                    break

                for member in members:
                    outcome = _apply_member(
                        cursor=cursor,
                        engine=engine,
                        vv_member_id=member["vv_member_id"],
                        watchlist_member_id=member["id"],
                        version_no=member["version_no"],
                        full_payload=member["full_payload"] or {},
                    )

                    processed += 1
                    since_commit += 1
                    if outcome == "versioned":
                        versioned += 1
                    elif outcome == "empty":
                        empty += 1
                    else:
                        skipped += 1

                    if since_commit >= commit_every:
                        connection.commit()
                        since_commit = 0

                last_member_id = members[-1]["id"]

            connection.commit()
        finally:
            cursor.close()
    except Exception:
        connection.rollback()
        logger.exception("Risk Category Initial Load failed.")
        raise
    finally:
        connection_pool.putconn(connection)

    result = {
        "mode": "initial_load",
        "processed_count": processed,
        "versioned_count": versioned,
        "skipped_count": skipped,
        "empty_count": empty,
        "deleted_count": 0,
    }
    logger.info(
        "Risk Category Initial Load: processed=%s versioned=%s skipped=%s "
        "empty=%s (%.1fs)",
        processed,
        versioned,
        skipped,
        empty,
        perf_counter() - started_at,
    )
    return result


# ---------------------------------------------------------------------------
# Incremental Processing
# ---------------------------------------------------------------------------

def run_incremental(
    effective_date=None,
    engine: RiskEngine | None = None,
    commit_every: int = COMMIT_EVERY,
) -> dict[str, int]:
    """Process one delta batch (ADD / UPDATE / DELETE).

    When ``effective_date`` is None the latest available batch is used
    (``MAX(effective_date)``), matching the guideline's execution scope. If there
    is no delta data at all, the ETL completes cleanly with zero counts.
    """
    started_at = perf_counter()
    engine = engine or RiskEngine()

    processed = 0
    versioned = 0
    skipped = 0
    empty = 0
    deleted = 0
    missing = 0
    since_commit = 0

    connection = connection_pool.getconn()

    try:
        connection.autocommit = False
        cursor = connection.cursor()

        try:
            if effective_date is None:
                effective_date = risk_repo.find_max_effective_date(cursor)

            if effective_date is None:
                connection.commit()
                logger.info("Risk Category ETL: no delta data; nothing to do.")
                return {
                    "mode": "incremental",
                    "effective_date": None,
                    "processed_count": 0,
                    "versioned_count": 0,
                    "skipped_count": 0,
                    "deleted_count": 0,
                    "missing_count": 0,
                }

            actions = risk_repo.find_delta_actions(cursor, effective_date)

            for delta in actions:
                action = (delta["action"] or "").upper()
                vv_member_id = delta["vv_member_id"]
                watchlist_member_id = delta["watchlist_member_id"]

                if action == "DELETE":
                    if _delete_member(cursor, vv_member_id):
                        deleted += 1
                    processed += 1
                    since_commit += 1

                elif action in {"ADD", "UPDATE"}:
                    member = risk_repo.find_member_by_id(
                        cursor, watchlist_member_id
                    )
                    if member is None:
                        # Delta references a member that is not there; record it
                        # and move on rather than aborting the whole batch.
                        missing += 1
                        logger.warning(
                            "Delta %s references missing watchlist_member_id=%s",
                            action,
                            watchlist_member_id,
                        )
                        continue

                    outcome = _apply_member(
                        cursor=cursor,
                        engine=engine,
                        vv_member_id=member["vv_member_id"],
                        watchlist_member_id=member["id"],
                        version_no=member["version_no"],
                        full_payload=member["full_payload"] or {},
                    )
                    processed += 1
                    since_commit += 1
                    if outcome == "versioned":
                        versioned += 1
                    elif outcome == "empty":
                        empty += 1
                    else:
                        skipped += 1

                else:
                    logger.warning("Unknown delta action %r; skipped.", action)
                    continue

                if since_commit >= commit_every:
                    connection.commit()
                    since_commit = 0

            connection.commit()
        finally:
            cursor.close()
    except Exception:
        connection.rollback()
        logger.exception(
            "Risk Category incremental ETL failed. effective_date=%s",
            effective_date,
        )
        raise
    finally:
        connection_pool.putconn(connection)

    result = {
        "mode": "incremental",
        "effective_date": effective_date,
        "processed_count": processed,
        "versioned_count": versioned,
        "skipped_count": skipped,
        "empty_count": empty,
        "deleted_count": deleted,
        "missing_count": missing,
    }
    logger.info(
        "Risk Category ETL %s: processed=%s versioned=%s skipped=%s empty=%s "
        "deleted=%s missing=%s (%.1fs)",
        effective_date,
        processed,
        versioned,
        skipped,
        empty,
        deleted,
        missing,
        perf_counter() - started_at,
    )
    return result
