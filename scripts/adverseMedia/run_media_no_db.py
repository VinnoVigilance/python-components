"""Run the full transform chain for ONE adverse-media dataset, DB-free.

Chains the media pipeline's real stages via the shared media harness:
    extract -> preprocess -> prenorm -> map -> postnorm
snapshotting each to data/raw/ and data/final/. The spider crawls the live source
capped at --max-records articles (default 15, enough to test pagination). --to stops
early, e.g. before mapping rules exist:
    python -m scripts.adverseMedia.run_media_no_db PCIJ_CORRUPTION_WATCH --to extract --preview

Usage:
    python -m scripts.adverseMedia.run_media_no_db NBI_PRESS_RELEASES
    python -m scripts.adverseMedia.run_media_no_db NBI_PRESS_RELEASES --max-records 20 --preview
    python -m scripts.adverseMedia.run_media_no_db NBI_PRESS_RELEASES --to extract
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.adverseMedia._media_harness import DEFAULT_MAX_RECORDS, STAGES, run_chain


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Full DB-free transform chain for one media dataset.")
    parser.add_argument("dataset_name", help="key under 'sources' in config/mediaSources.yaml")
    parser.add_argument("--to", choices=STAGES, default="postnorm", help="stop after this stage")
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS, help="cap the crawl to this many articles (default: no cap)")
    parser.add_argument("--preview", action="store_true", help="print the first --limit records per stage")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)

    run_chain(
        args.dataset_name,
        stop=args.to,
        max_records=args.max_records,
        preview=args.preview,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
