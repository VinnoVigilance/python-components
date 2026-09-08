# core_processing/spoke_sync_engine.py
from datetime import date
from typing import Dict, Any, Set
from spokeMapping import SPOKE_MAPPING
import sys
import os
#sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infrastructure.database.connection import get_db_connection
from config.loggingConfig import configure_platform_logging

# Initialize the centralized rotating logging infrastructure                                                            
logger = configure_platform_logging("Spoke_Sync_Engine")

# =====================================================================
# 1. AUTOMATED SQL PROCEDURAL GENERATORS
# =====================================================================
def get_not_null_columns(conn, table_name: str) -> Set[str]:
    """Dynamically queries Postgres to find columns that cannot accept NULLs."""
    schema, table = table_name.split('.') if '.' in table_name else ('public', table_name)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = %s AND table_name = %s AND is_nullable = 'NO'
        """, (schema, table))
        return {row[0] for row in cur.fetchall()}

def generate_daily_sync_sql(mapping: Dict[str, Any], conn) -> str:
    """Generates the Daily Synchronization Procedure with Null-Protection and Version-Gating Protection."""
    sql = [
        "CREATE OR REPLACE PROCEDURE delivery.sync_core_spoke_tables(p_effective_date date)",
        "LANGUAGE plpgsql AS $$",
        "BEGIN",
        "    CREATE TEMP TABLE tmp_spoke_targets ON COMMIT DROP AS",
        "    SELECT action, vv_member_id, watchlist_member_id",
        "    FROM delivery.watchlist_daily_delta_actions WHERE effective_date = p_effective_date;",
        ""
    ]
    
    # 1. VERSION-GATED DELETIONS
    # Only clear out data if the incoming version is >= what is currently live in the table.
    # If the table contains a newer version (e.g. 19th data), this delete safely skips it.
    target_tables = set(config.get("target_table", key) for key, config in mapping.items())
    for table_name in target_tables:
        sql.append(f"""
    DELETE FROM {table_name} target
    WHERE target.vv_member_id IN (SELECT vv_member_id FROM tmp_spoke_targets)
      AND target.watchlist_member_id <= (
          SELECT t.watchlist_member_id 
          FROM tmp_spoke_targets t 
          WHERE t.vv_member_id = target.vv_member_id 
          LIMIT 1
      );""")
    
    sql.append("")
    
    # 2. VERSION-GATED INSERTIONS WITH DYNAMIC NULL FILTERING
    for key, config in mapping.items():
        # Use target_table if it exists, otherwise fall back to the dictionary key                                                                                                                                                                                                                                         
        table_name = config.get("target_table", key)
        columns = ", ".join(config["columns"].keys())
        values = ", ".join(config["columns"].values())
        
        # Dynamically discover NOT NULL constraints for this specific table
        not_null_cols = get_not_null_columns(conn, table_name)
        
        # Build dynamic IS NOT NULL checks for the extracted JSON values
        null_filters = []
        for col_name, json_expr in config["columns"].items():
            if col_name in not_null_cols and "vv_member_id" not in col_name and "watchlist_member_id" not in col_name:
                null_filters.append(f"({json_expr}) IS NOT NULL")
                
        null_filter_sql = ""
        if null_filters:
            null_filter_sql = "\n      AND " + " AND ".join(null_filters)
        
        sql.append(f"""
    INSERT INTO {table_name} (vv_member_id, watchlist_member_id, {columns})
    SELECT t.vv_member_id, t.watchlist_member_id, {values}
    FROM tmp_spoke_targets t 
    JOIN core.watchlist_member wm ON t.watchlist_member_id = wm.id
    CROSS JOIN LATERAL jsonb_array_elements(wm.full_payload->'{config["json_array"]}') AS {config["alias"]}
    WHERE t.action IN ('ADD', 'UPDATE')
      AND NOT EXISTS (
          SELECT 1 FROM {table_name} current_spoke
          WHERE current_spoke.vv_member_id = t.vv_member_id
            AND current_spoke.watchlist_member_id > t.watchlist_member_id
      ){null_filter_sql};""")
        
    sql.append("END; $$;")
    return "\n".join(sql)

def generate_full_rebuild_sql(mapping: Dict[str, Any], conn) -> str:
    """Generates the Administrative Full Rebuild Procedure with Null-Protection & ANALYZE."""
    sql = [
        "CREATE OR REPLACE PROCEDURE delivery.rebuild_all_core_spoke_tables()",
        "LANGUAGE plpgsql AS $$",
        "BEGIN"
    ]
    
    # Extract unique target tables dynamically to prevent crashing on arbitrary virtual labels                                                                                          
    target_tables = set(config.get("target_table", key) for key, config in mapping.items())
    for table_name in target_tables:
        sql.append(f"    TRUNCATE TABLE {table_name} RESTART IDENTITY CASCADE;")
    
    sql.append("")
    for key, config in mapping.items():
        table_name = config.get("target_table", key)
        columns = ", ".join(config["columns"].keys())
        values = ", ".join(config["columns"].values())
        
        not_null_cols = get_not_null_columns(conn, table_name)
        null_filters = []
        for col_name, json_expr in config["columns"].items():
            if col_name in not_null_cols and "vv_member_id" not in col_name and "watchlist_member_id" not in col_name:
                null_filters.append(f"({json_expr}) IS NOT NULL")
                
        null_filter_sql = ""
        if null_filters:
            null_filter_sql = "\n      AND " + " AND ".join(null_filters)

        sql.append(f"""
    INSERT INTO {table_name} (vv_member_id, watchlist_member_id, {columns})
    SELECT wm.vv_member_id, wm.id, {values}
    FROM core.watchlist_member wm
    CROSS JOIN LATERAL jsonb_array_elements(wm.full_payload->'{config["json_array"]}') AS {config["alias"]}
    WHERE wm.is_current = TRUE
    AND wm.change_type != 'DELETED'
    {null_filter_sql};""")
    
    sql.append("")
    
    # 3. Update Statistics Immediately for the Query Planner
    for table_name in target_tables:
        sql.append(f"    ANALYZE {table_name};")
    
    sql.append("END; $$;")
    return "\n".join(sql)

# =====================================================================
# 2. RUNTIME EXECUTION MODULES
# =====================================================================
def deploy_database_procedures():
    """Compiles and updates the dynamic stored procedures inside PostgreSQL."""
    """Run this ONLY when setting up or modifying tables."""                                                        
    logger.info("Generating structural Spoke Synchronization procedures...")
    
    try:
        with get_db_connection() as conn:
            
            conn.autocommit = True
            
            # We now pass the connection so the generator can read the database schema
            daily_sql = generate_daily_sync_sql(SPOKE_MAPPING, conn)
            rebuild_sql = generate_full_rebuild_sql(SPOKE_MAPPING, conn)
            
            with conn.cursor() as cur:
                # 1. Ensure the high-level logging table exists
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS core.spoke_run_log (
                        run_date DATE PRIMARY KEY,
                        status VARCHAR(50) NOT NULL,
                        completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                # 2. Deploy the generated dynamic procedures
                cur.execute(daily_sql)
                cur.execute(rebuild_sql)
                
        logger.info("Database stored procedures successfully compiled and deployed.")
    except Exception as err:
        logger.error(f"Critical failure while deploying stored database routines: {err}")
        raise

def execute_daily_sync(target_date: date):
    """Executes incremental spoke processing for a specific business date and logs the daily watermark."""
    logger.info(f"Requesting Daily Spoke Sync execution for operational date: {target_date}")
    try:
        with get_db_connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                # 1. Run the massive single-transaction sync
                cur.execute("CALL delivery.sync_core_spoke_tables(%s);", (target_date,))
                
                # 2. Drop the single watermark so downstream tasks know it's safe to proceed
                cur.execute("""
                    INSERT INTO core.spoke_run_log (run_date, status)
                    VALUES (%s, 'ALL_SPOKES_COMPLETE')
                    ON CONFLICT (run_date) 
                    DO UPDATE SET status = 'ALL_SPOKES_COMPLETE', completed_at = CURRENT_TIMESTAMP;
                """, (target_date,))
                
        logger.info(f"Daily Spoke Sync operations successfully completed for {target_date}.")
    except Exception as err:
        logger.error(f"Daily Spoke Sync execution failed for date {target_date}: {err}")
        raise

def execute_full_rebuild():
    """Truncates all spokes and recalculates entire tables from scratch."""
    """Run this only for disaster recovery or full system resets."""                                                                
    logger.warning("Initiating Full Spoke Rebuild. This will truncate all data...")
    try:
        with get_db_connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("CALL delivery.rebuild_all_core_spoke_tables();")
                # 2. Drop the single watermark so downstream tasks know it's safe to proceed
                cur.execute("""
                    INSERT INTO core.spoke_run_log (run_date, status)
                    VALUES (CURRENT_TIMESTAMP, 'ALL_SPOKES_COMPLETE')
                    ON CONFLICT (run_date) 
                    DO UPDATE SET status = 'ALL_SPOKES_COMPLETE', completed_at = CURRENT_TIMESTAMP;
                """)
        logger.info("Full administrative data rebuild completed successfully.")
    except Exception as err:
        logger.error(f"Administrative database rebuild crashed: {err}")
        raise

if __name__ == "__main__":
    # -------------------------------------------------------------
    # CONTROL PANEL: Uncomment the specific operation you want to trigger
    # -------------------------------------------------------------
    
    # 1. Update/Deploy structural changes to Postgres (Run once, or when adding new tables)
    deploy_database_procedures()
    
    # 2. Trigger daily calculation manually for a date (Execute normal daily sync)
    execute_daily_sync(date(2026, 7, 21))
    
    # 3. Administrative full rebuild (Only when necessary: Emergency Disaster Recovery Rebuild)
    # execute_full_rebuild()                 