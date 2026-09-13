"""Stage 2 - EXTRACT: raw records via the parser (file sources) or the spider
(crawler / saved_html sources), DB-free. Writes data/raw/<LIST>_extracted.jsonl.

Usage:
    python -m scripts.stage_extract EU-MOST-WANTED
    python -m scripts.stage_extract DFAT --source-file data/downloads/DFAT_2026....xlsx
    python -m scripts.stage_extract EU-MOST-WANTED --preview
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._harness import finish, get_config, is_crawler, stage_extract, write_meta


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Extract raw records (parser for files, spider for crawlers)."
    )
    parser.add_argument("list_name", help="key in WATCHLIST_CONFIGS")
    parser.add_argument("--source-file", default=None, help="parse this exact file (file sources)")
    parser.add_argument("--out", default=None, help="override output artifact path")
    parser.add_argument("--preview", action="store_true", help="print the first --limit records")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)

    config = get_config(args.list_name)
    records, source_file = stage_extract(config, source_file=args.source_file)
    if not is_crawler(config):
        write_meta(args.list_name, source_file=source_file)
    print(f"source     : {source_file}")
    finish(args.list_name, "extract", records, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
