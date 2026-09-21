"""Stage 3 - PREPROCESS: run the real PreProcessingEngine (config["preprocessing"])
on the extract artifact. This is the shape mapping sees. DB-free.
Reads data/raw/<LIST>_extracted.jsonl, writes data/final/<LIST>_preprocessed.jsonl.

Usage:
    python -m scripts.watchlist.stage_preprocess EU-MOST-WANTED
    python -m scripts.watchlist.stage_preprocess EU-MOST-WANTED --preview
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.watchlist._harness import (
    finish,
    get_config,
    load_stage_input,
    read_meta,
    stage_parser,
    stage_preprocess,
)


def main(argv=None) -> None:
    args = stage_parser("Run PREPROCESSING on the extract artifact.").parse_args(argv)
    config = get_config(args.list_name)
    records = load_stage_input(args.list_name, "preprocess", args.infile)
    source_file = read_meta(args.list_name).get("source_file")
    processed = stage_preprocess(config, records, source_file=source_file)
    finish(args.list_name, "preprocess", processed, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
