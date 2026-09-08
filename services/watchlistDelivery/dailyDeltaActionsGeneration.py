from datetime import date, timedelta
import psycopg2 
import sys
import os
#sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from infrastructure.database.connection import get_db_connection
from config.loggingConfig import configure_platform_logging

# 1. Initialize your custom rotating file logger
logger = configure_platform_logging("Watchlist_Delta")

def _generate_date_range(start_date: date, end_date: date):
    """Yields sequential dates from start_date to end_date (inclusive)."""
    delta_days = int((end_date - start_date).days)
    for n in range(delta_days + 1):
        yield start_date + timedelta(n)

def generate_watchlist_delta_actions(from_date: date = None, to_date: date = None):
    """
    Executes the PostgreSQL procedure to generate the delta.
    - No dates: Defaults to yesterday.
    - Only from_date: Runs for that single date.
    - Both dates: Backfills sequentially over the range.
    """
    # Default to yesterday if no from_date is provided (e.g., executing at 00:05)                                                                                                                                                                                                                                       
    start_date = from_date or (date.today() - timedelta(days=1))
    # If no to_date is provided, default to the start_date for a single-day run                                                                                                                                                                                                                                 
    end_date = to_date or start_date
    
    if start_date > end_date:
        raise ValueError("The from_date cannot be later than the to_date.")
        
    logger.info(f"Initiating delta generation from {start_date} to {end_date}")
    
    # Initialize target_date so the exception block always has a valid date to log
    target_date = start_date 
    
    try:
        # 2. Use your custom thread-safe connection pool
        with get_db_connection() as conn:
            # Autocommit must be True when calling PostgreSQL stored procedures
            conn.autocommit = True 
            
            with conn.cursor() as cur:
                for target_date in _generate_date_range(start_date, end_date):
                    logger.info(f"Generating delta for effective date: {target_date}...")
                    
                    # Call the bulletproof stored procedure for the specific date
                    cur.execute(
                        "CALL delivery.generate_watchlist_daily_delta_actions(%s);", 
                        (target_date,)
                    )
                    
        logger.info("Delta generation successfully completed.")
        
    except Exception as err:
        # 3. Log the exact business date that caused the failure before passing the error up
        logger.error(f"Database error halted process. Failed while processing date {target_date}. Error: {err}")
        raise

# Example execution for your automated job
if __name__ == "__main__":
    #generate_watchlist_delta_actions()
    generate_watchlist_delta_actions(date(2026, 7, 27))