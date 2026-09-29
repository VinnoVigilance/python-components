"""Stage 1 - EXTRACT: crawl an adverse-media source DB-free and write its raw records.

The spider fetches AND extracts, capped at --max-records articles (default 15, enough
to cross a listing page and exercise pagination). Saves article HTML under
data/downloads/ and the extracted source fields to data/raw/<DATASET>_extracted.jsonl.

Usage:
    python -m scripts.adverseMedia.stage_media_extract NBI_PRESS_RELEASES
    python -m scripts.adverseMedia.stage_media_extract NBI_PRESS_RELEASES --max-records 20 --preview
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts._shared import finish
from scripts.adverseMedia._media_harness import DEFAULT_MAX_RECORDS, get_media_config, stage_extract


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Crawl an adverse-media source (DB-free).")
    parser.add_argument("dataset_name", help="key under 'sources' in config/mediaSources.yaml")
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS, help="cap the crawl to this many articles (default: no cap)")
    parser.add_argument("--out", default=None, help="override output artifact path")
    parser.add_argument("--preview", action="store_true", help="print the first --limit records")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)

    _, source_config = get_media_config(args.dataset_name)
    records = stage_extract(source_config, max_records=args.max_records)
    finish(args.dataset_name, "extract", records, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
