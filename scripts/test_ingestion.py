"""
Test the DOWNLOAD / INGESTION step for a single watchlist (no DB, no parsing).

It runs the SAME acquisition the real pipeline uses --
``services/watchlistPipeline/watchlistFileService.acquire_source_file()`` --
which dispatches on the list's ``download_method``:

    HTTPS / (blank)   -> ingestion.downloader        (plain file download)
    API               -> ingestion.apiCollector      (paged API snapshot)
    BYPASS            -> ingestion.bypassCollector    (stealth browser)
    Manual/local_path -> uses the already-present local file

So testing any ingestion method is just: set LIST_NAME to that list and run.
It prints which method was used and the saved file's path + size. No database,
no normalization.

Usage:
    # set LIST_NAME in main() below, then:
    python -m scripts.test_ingestion
    python scripts/test_ingestion.py
    # or override once from the command line:
    python scripts/test_ingestion.py ATC-DESIGNATED-TERRORIST-INDIVIDUALS
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running by path or by module: put the repo root on sys.path so the
# first-party packages import either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.loggingConfig import configure_logging
from ingestion.downloader import interface as downloader
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistFileService


def test_ingestion(list_name: str) -> None:
    """Acquire the source file for one list and report the result."""
    if list_name not in WATCHLIST_CONFIGS:
        available = ", ".join(sorted(WATCHLIST_CONFIGS))
        raise SystemExit(
            f"Unknown list: {list_name}\nAvailable lists: {available}"
        )

    config = WATCHLIST_CONFIGS[list_name]

    print(f"List           : {list_name}")
    print(f"Source         : {config.get('source_name')}")
    print(f"Download method: {config.get('download_method', '(none)')}")
    print(f"URL            : {config.get('url', '-')}")
    print("-" * 60)

    # Same acquisition the pipeline runs; it picks download / API / bypass /
    # manual based on the config's download_method.
    source_file = watchlistFileService.acquire_source_file(
        config=config,
        downloader=downloader,
    )

    size = source_file.stat().st_size
    print("-" * 60)
    print("INGESTION OK")
    print(f"Saved file     : {source_file}")
    print(f"File size      : {size:,} bytes")


def main(argv=None) -> None:
    # Send ingestion logs (including the bypass collector's) to the terminal.
    configure_logging()

    # --- set the list you want to test here ---
    LIST_NAME = "GPPB-BLACKLISTED-ENTITIES"

    # Optional one-off override from the command line, e.g.
    #   python scripts/test_ingestion.py DFAT
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        LIST_NAME = argv[0]

    test_ingestion(LIST_NAME)


if __name__ == "__main__":
    main()
