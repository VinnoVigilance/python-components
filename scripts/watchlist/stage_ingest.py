"""Stage 1 - INGEST: acquire the source file (file / API / bypass), DB-free.

Crawler sources have no separate ingest step: the spider fetches AND extracts, so
run stage_extract for those instead.

Usage:
    python -m scripts.watchlist.stage_ingest US-STATE-TERRORIST-EXCLUSION
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.watchlist._harness import get_config, is_crawler, stage_ingest, write_meta


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Acquire the source file (file / API / bypass).")
    parser.add_argument("list_name", help="key in WATCHLIST_CONFIGS")
    args = parser.parse_args(argv)

    config = get_config(args.list_name)

    if is_crawler(config):
        print(
            f"{args.list_name} is a CRAWLER source: ingestion is fused with extraction "
            f"(the spider fetches AND extracts).\n"
            f"Run: python -m scripts.watchlist.stage_extract {args.list_name}"
        )
        return

    source_file = stage_ingest(config)
    write_meta(args.list_name, source_file=source_file)
    print(f"ingest     : source file -> {source_file}")
    print(f"size       : {source_file.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
