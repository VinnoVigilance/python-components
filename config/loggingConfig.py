import logging
import sys
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

def configure_logging(log_dir: str = "logs") -> None:
    """
    Configures centralized logging for the application.
    Outputs to stdout (for Docker Desktop/terminal) and a rotating file (for historical audits).
    """
    # 1. Start with the console output handler
    handlers_list = [
        logging.StreamHandler(sys.stdout)
    ]
    
    # 2. (physical rotating file handler) Use pathlib to create an absolute path that cron and Task Scheduler understand
    # Path(__file__).parent gets the exact folder this script lives in, regardless of where cron executes it from.
    # Path(__file__).resolve()        -> .../python-components/config/loggingConfig.py
    # Path(__file__).resolve().parent -> .../python-components/config
    # .parent.parent                  -> .../python-components
    project_root = Path(__file__).resolve().parent.parent
    log_path = project_root / log_dir 
    
    try:
        # pathlib's way of doing os.makedirs(exist_ok=True)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # pathlib handles the slashes using the '/' operator (replaces os.path.join)
        file_path = log_path / "python_pipeline_events.log"
        
        file_handler = TimedRotatingFileHandler(
            filename=str(file_path),
            when="midnight",
            interval=1,
            backupCount=120,
            encoding="utf-8"
        )
        file_handler.suffix = "%Y-%m-%d.log"
        
        # Add the file handler to the mailing list
        handlers_list.append(file_handler)
    except Exception as e:
        # Failsafe: If folder permissions fail, just print a warning and continue with stdout                                                                                     
        print(f"Warning: Could not initialize physical file logging: {e}")

    # 3. Apply the config. Python will broadcast to every handler in handlers_list.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers_list,
        force=True # Good practice if another module accidentally called basicConfig first
    )