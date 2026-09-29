import argparse
import logging
import sys
from pathlib import Path
from pprint import pprint
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from config.loggingConfig import configure_logging
from services.searchSync.watchlistSearchSyncService import run_sync


logger = logging.getLogger(__name__)


DEFAULT_BATCH_SIZE = 1000


def run_watchlist_search_sync_pipeline(
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Run the PostgreSQL to Elasticsearch watchlist synchronization.

    By default, changes are applied to Elasticsearch.
    """

    return run_sync(
        batch_size=batch_size,
        dry_run=dry_run,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Synchronize current watchlist members "
            "from PostgreSQL to Elasticsearch."
        )
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            "Number of PostgreSQL records processed in each batch. "
            f"Default: {DEFAULT_BATCH_SIZE}"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Calculate ADD, UPDATE, SKIP and DELETE counts "
            "without changing Elasticsearch."
        ),
    )

    return parser.parse_args()


def main() -> None:
    configure_logging()

    args = _parse_args()

    try:
        result = run_watchlist_search_sync_pipeline(
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )

        pprint(result)

    except Exception:
        logger.exception(
            "Watchlist search synchronization pipeline failed."
        )
        raise


if __name__ == "__main__":
    main()