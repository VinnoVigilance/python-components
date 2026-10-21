# post_ingestion_handler.py
from datetime import date, timedelta
import sys
import os
from pathlib import Path
from services.watchlistDelivery.watchlistDailyDeltaActionsGeneration import generate_watchlist_delta_actions
from services.watchlistDelivery.mediaDailyDeltaActionsGeneration import generate_media_delta_actions
from services.watchlistDelivery.spokeSyncEngine import execute_daily_sync, deploy_database_procedures, execute_full_rebuild
#sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infrastructure.database.connection import get_db_connection
import logging
from config.loggingConfig import configure_logging, JobLog

configure_logging()
logger = logging.getLogger("post_ingestion_spokes_and_deltas")

# =====================================================================
# 1. UTILITY FUNCTIONS & GATEKEEPER
# =====================================================================
def _generate_date_range(start_date: date, end_date: date):
    """Yields sequential dates from start_date to end_date (inclusive)."""
    delta_days = int((end_date - start_date).days)
    for n in range(delta_days + 1):
        yield start_date + timedelta(n)

def get_watermark_dates():
    """
    Queries the database to find the last processed delta and the core data boundaries.
    Returns: (watchlist_last_processed_date, watchlist_latest_available_core_date, watchlist_genesis_core_date, media_last_processed_date, media_latest_available_core_date, media_genesis_core_date)
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 1. Find the last date we successfully built a delta for
                cur.execute("SELECT MAX(effective_date) FROM delivery.watchlist_daily_delta_actions;")
                watchlist_last_processed = cur.fetchone()[0]
                
                # 2. Find the newest date in core
                cur.execute("SELECT MAX(DATE(created_at)) FROM core.watchlist_member;")
                watchlist_latest_core = cur.fetchone()[0]
                
                # 3. Find the absolute oldest date in core (Genesis Date)
                cur.execute("SELECT MIN(DATE(created_at)) FROM core.watchlist_member;")
                watchlist_genesis_core = cur.fetchone()[0]
                
                # 4. Find the last date we successfully built a delta for
                cur.execute("SELECT MAX(effective_date) FROM delivery.media_daily_delta_actions;")
                media_last_processed = cur.fetchone()[0]

                # 5. Find the newest date in core
                cur.execute("SELECT MAX(DATE(created_at)) FROM core.media_record;")
                media_latest_core = cur.fetchone()[0]

                # 6. Find the absolute oldest date in core (Genesis Date)
                cur.execute("SELECT MIN(DATE(created_at)) FROM core.media_record;")
                media_genesis_core = cur.fetchone()[0]

        return watchlist_last_processed, watchlist_latest_core, watchlist_genesis_core, media_last_processed, media_latest_core, media_genesis_core
    except Exception as err:
        logger.error(f"Failed to retrieve watermark dates from database: {err}")
        raise

def check_spokes_finished(target_date: date):
    """
    Checks the granular log table to see if the 'ALL_SPOKES_COMPLETE' 
    watermark was logged for the target date.
    """
    try:
        # Utilizing the existing connection pool manager
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Updated to match the refined schema (no spoke_table column)
                cursor.execute("""
                    SELECT status FROM core.spoke_run_log 
                    WHERE run_date = %s AND status = 'ALL_SPOKES_COMPLETE'
                """, (target_date,))
                result = cursor.fetchone()
                
                if result and result[0] == 'ALL_SPOKES_COMPLETE':
                    logger.info(f"Watermark verified: All spokes successfully synced for {target_date}.")
                    return True
                else:
                    logger.warning(f"Watermark missing. Spokes are not finished for {target_date}.")
                    return False
                    
    except Exception as e:
        logger.error(f"Error checking spoke watermark table: {e}", exc_info=True)
        return False

def rebuild_all_spokes():
    """
    Administrative tool to force a complete truncation and rebuild of all Spoke tables 
    directly from the current core state. No dates required.
    """
    logger.warning("!!! INITIATING FULL SPOKE REBUILD FROM SCRATCH !!!")
    
    try:
        # 1. Deploy the latest SQL logic to PostgreSQL first
        logger.info("Deploying/Verifying dynamic PostgreSQL Spoke Procedures...")
        deploy_database_procedures()
        
        # 2. Wipes all tables and rebuilds directly from core.watchlist_member
        execute_full_rebuild()
        logger.info("!!! SPOKE HISTORICAL REBUILD COMPLETED SUCCESSFULLY !!!")
    except Exception as err:
        logger.critical(f"Historical rebuild crashed: {err}")
        sys.exit(1)        
        
# =====================================================================
# 2. THE SELF-HEALING AUTOMATED PIPELINE (RUNS POST-INGESTION)
# =====================================================================
def _run_watchlist_pipeline(last_processed, latest_core, genesis_core):
    """Generate watchlist deltas and synchronize their search spokes."""
    if not latest_core:
        logger.info("No watchlist core data; skipping watchlist delivery.")
        return
    if last_processed and last_processed >= latest_core:
        logger.info("Watchlist deltas are up to date through %s.", last_processed)
        return
    start_date = last_processed + timedelta(days=1) if last_processed else genesis_core
    deploy_database_procedures()
    for target_date in _generate_date_range(start_date, latest_core):
        generate_watchlist_delta_actions(from_date=target_date, to_date=target_date)
        execute_daily_sync(target_date)
    if not check_spokes_finished(latest_core):
        raise RuntimeError(f"Watchlist spokes are incomplete for {latest_core}.")
    _refresh_search_view()


def _run_media_pipeline(last_processed, latest_core, genesis_core):
    """Generate media deltas independently of watchlist delivery readiness."""
    if not latest_core:
        logger.info("No media core data; skipping media delta generation.")
        return
    if last_processed and last_processed >= latest_core:
        logger.info("Media deltas are up to date through %s.", last_processed)
        return
    start_date = last_processed + timedelta(days=1) if last_processed else genesis_core
    for target_date in _generate_date_range(start_date, latest_core):
        generate_media_delta_actions(from_date=target_date, to_date=target_date)


def _refresh_search_view():
    """Refresh screening search without leaking autocommit into pooled callers."""
    with get_db_connection() as conn:
        previous_autocommit = conn.autocommit
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY core.mv_screening_member_search;")
        finally:
            conn.autocommit = previous_autocommit


def run_automated_pipeline():
    """Attempt both delivery domains and propagate any incomplete execution."""
    watermarks = get_watermark_dates()
    failures = []
    for domain, runner, dates in (
        ("watchlist", _run_watchlist_pipeline, watermarks[:3]),
        ("media", _run_media_pipeline, watermarks[3:]),
    ):
        try:
            runner(*dates)
        except Exception as error:
            failures.append((domain, error))
            logger.exception("Post-ingestion %s processing failed.", domain)
    if failures:
        domains = ", ".join(domain for domain, _ in failures)
        raise RuntimeError(f"Post-ingestion processing failed for: {domains}.") from failures[0][1]
    logger.info("Post-ingestion watchlist and media processing completed.")

# =====================================================================
# 3. DISASTER RECOVERY & HISTORICAL BACKFILL
# =====================================================================
def execute_historical_backfill(from_date: date, to_date: date):
    """
    Administrative tool to force a sequential rebuild of both the Watchlist Delta
    and the Spoke tables for a specific date range.
    """
    if from_date > to_date:
        logger.error("Backfill failed: from_date cannot be later than to_date.")
        return

    logger.warning(f"!!! INITIATING HISTORICAL REBUILD FROM {from_date} TO {to_date} !!!")
    
    try:
        # Loop: Rebuild the watchlist daily deltas sequentially
        for target_date in _generate_date_range(from_date, to_date):
            logger.info(f"Rebuilding watchlist delta action state for: {target_date}")
            generate_watchlist_delta_actions(from_date=target_date, to_date=target_date)
        
        # Sync the spokes once to the final targeted state.
        logger.info(f"Backfill deltas complete. Running refilling all spokes")
        # This ensures any new tables or attributes added to SPOKE_MAPPING are instantly pushed to Postgres.
        logger.info("Deploying/Verifying dynamic PostgreSQL Spoke Procedures...")
        deploy_database_procedures()
        logger.info("Truncating and Refilling Spokes...")
        rebuild_all_spokes()
        
        # Refresh the Materialized View ONCE, after all data is safely in the Spoke tables
        logger.info("Refreshing Screening Materialized View...")
        _refresh_search_view()
        logger.info("Materialized View successfully refreshed.")
            
        logger.info("!!! HISTORICAL REBUILD COMPLETED SUCCESSFULLY !!!")
    except Exception as err:
        logger.critical(f"Historical rebuild crashed on {target_date}: {err}")
        sys.exit(1)


def execute_media_historical_backfill(from_date: date, to_date: date):
    """
    Administrative tool to force a sequential rebuild of both the Media Delta for a specific date range.
    """
    if from_date > to_date:
        logger.error("Backfill media failed: from_date cannot be later than to_date.")
        return

    logger.warning(f"!!! INITIATING MEDIA HISTORICAL REBUILD FROM {from_date} TO {to_date} !!!")

    try:
        # Loop: Rebuild the media daily deltas sequentially
        for target_date in _generate_date_range(from_date, to_date):
            logger.info(f"Rebuilding media delta action state for: {target_date}")
            generate_media_delta_actions(from_date=target_date, to_date=target_date)


        logger.info("!!! MEDIA HISTORICAL REBUILD COMPLETED SUCCESSFULLY !!!")
    except Exception as err:
        logger.critical(f"Media Historical rebuild crashed on {target_date}: {err}")
        sys.exit(1)


    
if __name__ == "__main__":
    # -----------------------------------------------------------------
    # This is the ONLY function your orchestrator/cron job triggers.
    # It will automatically detect failures, find the missing days, 
    # and sequential loop through them until it is caught up.
    # -----------------------------------------------------------------
    with JobLog(job_name="post_ingestion_spoke_and_delta", log_dir=Path("logs"), timezone_name="UTC") as log:
        
        log.event("JOB_STARTED", started_at=log.stamp(), sources="core_to_spokes", timezone="UTC")
        
        try:
            run_automated_pipeline()
            #execute_historical_backfill(date(2026, 10, 4), date(2026, 10, 13))
            #execute_media_historical_backfill(date(2026, 10, 4), date(2026, 10, 13))
            log.event("JOB_FINISHED", status="SUCCESS")
            
        except Exception as err:
            log.event("ATTEMPT_FAILED", error=err)
            raise
    # -----------------------------------------------------------------
    # DISASTER RECOVERY TOOL
    # If data is corrupted between Jan 1 and Jan 5, uncomment and run this.
    # It will rebuild the delta and the spokes sequentially.
    # -----------------------------------------------------------------
