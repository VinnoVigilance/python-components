"""Run the full transform chain for ONE watchlist, DB-free.

Chains the pipeline's real stages via the shared harness:
    extract -> preprocess -> prenorm -> map -> postnorm
snapshotting each to data/raw/ and data/final/ (previous testing layout). --to stops
early (e.g. before mapping rules exist). For crawler sources the spider does extract;
everything else parses the latest download.

Usage:
    python -m scripts.watchlist.run_pipeline_no_db EU-MOST-WANTED
    python -m scripts.watchlist.run_pipeline_no_db EU-MOST-WANTED --to preprocess
    python -m scripts.watchlist.run_pipeline_no_db DFAT --source-file <path> --preview
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.watchlist._harness import STAGES, run_chain


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Full DB-free transform chain for one list.")
    parser.add_argument("list_name", help="key in WATCHLIST_CONFIGS")
    parser.add_argument("--to", choices=STAGES[1:], default="postnorm", help="stop after this stage")
    parser.add_argument("--source-file", default=None, help="parse this exact file (file sources)")
    parser.add_argument("--preview", action="store_true", help="print the first --limit records per stage")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)

    run_chain(
        args.list_name,
        source_file=args.source_file,
        stop=args.to,
        preview=args.preview,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
