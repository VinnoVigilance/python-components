import logging
import sys
import os
from logging.handlers import TimedRotatingFileHandler

def configure_logging(log_dir: str = "/app/logs") -> None:
    """
    Configures centralized logging for the application.
    Outputs to stdout (for Docker Desktop) and a rotating file (for historical audits).
    """
    # 1. Start with their original stdout handler
    handlers_list = [
        logging.StreamHandler(sys.stdout)
    ]
    
    # 2. Add your physical rotating file handler
    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=os.path.join(log_dir, "platform_events.log"),
            when="midnight",
            interval=1,
            backupCount=120
        )
        file_handler.suffix = "%Y-%m-%d.log"
        handlers_list.append(file_handler)
    except Exception as e:
        # Failsafe: If folder permissions fail, just print a warning and continue with stdout
        print(f"Warning: Could not initialize physical file logging: {e}")

    # 3. Apply the config using their original format string
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
        handlers=handlers_list,
    )