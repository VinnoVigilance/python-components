"""Run the ingestion step for one watchlist (no DB, no parsing), using the same
``acquire_source_file`` the pipeline uses. Set LIST_NAME below or pass it as an
argument: ``python scripts/test_ingestion.py DFAT``."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.loggingConfig import configure_logging
from ingestion.downloader import interface as downloader
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistFileService


def _require_config(list_name: str) -> dict:
    """Look up a list's config or exit with the available names."""
    if list_name not in WATCHLIST_CONFIGS:
        available = ", ".join(sorted(WATCHLIST_CONFIGS))
        raise SystemExit(
            f"Unknown list: {list_name}\nAvailable lists: {available}"
        )

    return WATCHLIST_CONFIGS[list_name]


def test_ingestion(list_name: str) -> None:
    """Acquire the source file for one list and report the result."""
    config = _require_config(list_name)

    print(f"List           : {list_name}")
    print(f"Source         : {config.get('source_name')}")
    print(f"Download method: {config.get('download_method', '(none)')}")
    print(f"URL            : {config.get('url', '-')}")
    print("-" * 60)

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
    configure_logging()

    LIST_NAME = "INTERPOL-RED-NOTICES"

    argv = sys.argv[1:] if argv is None else argv

    if argv:
        LIST_NAME = argv[0]

    test_ingestion(LIST_NAME)


if __name__ == "__main__":
    main()
