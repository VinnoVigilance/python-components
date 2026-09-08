# post_ingestion_handler.py
from datetime import date, timedelta
import sys
import os
from dailyDeltaActionsGeneration import generate_watchlist_delta_actions 
from spokeSyncEngine import execute_daily_sync, deploy_database_procedures, execute_full_rebuild
#sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infrastructure.database.connection import get_db_connection
from config.loggingConfig import configure_platform_logging

logger = configure_platform_logging("post_ingestion_handler")

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
    Returns: (last_processed_date, latest_available_core_date, genesis_core_date)                                                          
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 1. Find the last date we successfully built a delta for
                cur.execute("SELECT MAX(effective_date) FROM delivery.watchlist_daily_delta_actions;")
                last_processed = cur.fetchone()[0]
                
                # 2. Find the newest date in core
                cur.execute("SELECT MAX(DATE(created_at)) FROM core.watchlist_member;")
                latest_core = cur.fetchone()[0]
                
                # 3. Find the absolute oldest date in core (Genesis Date)
                cur.execute("SELECT MIN(DATE(created_at)) FROM core.watchlist_member;")
                genesis_core = cur.fetchone()[0]
                
        return last_processed, latest_core, genesis_core
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
        logger.info("!!! HISTORICAL REBUILD COMPLETED SUCCESSFULLY !!!")
    except Exception as err:
        logger.critical(f"Historical rebuild crashed: {err}")
        sys.exit(1)        
        
# =====================================================================
# 2. THE SELF-HEALING AUTOMATED PIPELINE (RUNS POST-INGESTION)
# =====================================================================
def run_automated_pipeline():
    """
    Detects gaps between the last processed date and the newest ingested data, handles day-zero starts natively, and sequentially processes data.
    Sequentially backfills all missing days up to the present.                                                          
    """
    logger.info("=== WAKING UP: CHECKING PIPELINE WATERMARKS ===")
    
    # Automatically compile and deploy stored procedures on every run.
    # This ensures any new tables or attributes added to SPOKE_MAPPING are instantly pushed to Postgres.
    logger.info("Deploying/Verifying dynamic PostgreSQL Spoke Procedures...")
    deploy_database_procedures()
    
    last_processed, latest_core, genesis_core = get_watermark_dates()
    
    if not latest_core:
        logger.warning("No data found in core.watchlist_member. Pipeline sleeping.")
        return

    # If the database is brand new (Day Zero), auto-set the start date to the Genesis Date
    if not last_processed:
        logger.info(f"Day Zero detected. Commencing initial pipeline build from {genesis_core}.")
        start_date = genesis_core
    else:
        # If the delta is already caught up, do nothing
        if last_processed >= latest_core:
            logger.info(f"Pipeline is fully up to date (Watermark: {last_processed}). Sleeping.")
            return
        
        # Calculate the gap (e.g., if last processed was the 10th, start on the 11th)
        start_date = last_processed + timedelta(days=1)
        
    end_date = latest_core
    logger.info(f"Processing backlog sequentially from {start_date} to {end_date}")
    
    try:
        # Sequentially process each missing day to maintain strict chronological state                                                                              
        for target_date in _generate_date_range(start_date, end_date):
            logger.info(f"--- Generating Delta Actions for Date: {target_date} ---")
            
            # Step 1: Calculate the Delta
            generate_watchlist_delta_actions(from_date=target_date, to_date=target_date)
            
            # Step 2: Push the Delta to the Spoke Tables
            # Spokes only care about the latest state, so we sync directly to end_date.
            logger.info(f"Delta actions complete. Syncing search spokes to state: {target_date}")
            execute_daily_sync(target_date)
        
        # Step 3: THE GATEKEEPER - Verify spoke execution before proceeding
        logger.info("=== PIPELINE CAUGHT UP, VERIFYING SPOKES ===")
        if not check_spokes_finished(end_date):
            logger.warning("Aborting post-ingestion delivery tasks: Spoke tables are not fully synced yet.")
            return
        
        # Refresh the Materialized View ONCE, after all data is safely in the Spoke tables
        logger.info("Refreshing Screening Materialized View...")
        with get_db_connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY core.mv_screening_member_search;")
        logger.info("Materialized View successfully refreshed.")
        
        # Step 4: Proceed with extraction tasks
        #logger.info("Proceeding with data extraction and delivery tasks...")
        # Add your extraction/queue logic here (e.g., export_customer_files(end_date))
        
        logger.info("=== PIPELINE FULLY COMPLETED AND SYNCHRONIZED ===")
        
    except Exception as err:
        logger.critical(f"FATAL ERROR during pipeline execution. Halted: {err}")
        sys.exit(1)

# =====================================================================
# 3. DISASTER RECOVERY & HISTORICAL BACKFILL
# =====================================================================
def execute_historical_backfill(from_date: date, to_date: date):
    """
    Administrative tool to force a sequential rebuild of both the Delta 
    and the Spoke tables for a specific date range.
    """
    if from_date > to_date:
        logger.error("Backfill failed: from_date cannot be later than to_date.")
        return

    logger.warning(f"!!! INITIATING HISTORICAL REBUILD FROM {from_date} TO {to_date} !!!")
    
    try:
        # Loop: Rebuild the daily deltas sequentially                                               
        for target_date in _generate_date_range(from_date, to_date):
            logger.info(f"Rebuilding delta action state for: {target_date}")
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
        with get_db_connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY core.mv_screening_member_search;")
        logger.info("Materialized View successfully refreshed.")
            
        logger.info("!!! HISTORICAL REBUILD COMPLETED SUCCESSFULLY !!!")
    except Exception as err:
        logger.critical(f"Historical rebuild crashed on {target_date}: {err}")
        sys.exit(1)

if __name__ == "__main__":
    # -----------------------------------------------------------------
    # This is the ONLY function your orchestrator/cron job triggers.
    # It will automatically detect failures, find the missing days, 
    # and sequential loop through them until it is caught up.
    # -----------------------------------------------------------------
    
    
    #execute_historical_backfill(date(2026, 8, 1), date(2026, 8, 7))
    
    run_automated_pipeline()
    
    #rebuild_all_spokes()
    #execute_historical_backfill(date(2025, 1, 1),date(2026, 7, 26))
    # -----------------------------------------------------------------
    # DISASTER RECOVERY TOOL
    # If data is corrupted between Jan 1 and Jan 5, uncomment and run this.
    # It will rebuild the delta and the spokes sequentially.
    # -----------------------------------------------------------------
    # execute_historical_backfill(date(2026, 1, 1), date(2026, 1, 5))