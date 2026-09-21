"""Stage 6 - POST-NORMALIZATION: run the real PostNormalizationEngine
(postNormalization.xlsx) on the mapping artifact -> canonical/final records. DB-free.
Reads data/raw/<LIST>_mapped.jsonl, writes data/final/<LIST>_final.jsonl.

Usage:
    python -m scripts.watchlist.stage_postnorm EU-MOST-WANTED --preview
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.watchlist._harness import (
    finish,
    get_config,
    load_stage_input,
    stage_parser,
    stage_postnorm,
)


def main(argv=None) -> None:
    args = stage_parser("Run POST-NORMALIZATION on the mapping artifact.").parse_args(argv)
    config = get_config(args.list_name)
    records = load_stage_input(args.list_name, "postnorm", args.infile)
    canonical = stage_postnorm(config, records)
    finish(args.list_name, "postnorm", canonical, out=args.out, preview=args.preview, limit=args.limit)
    print(f"EntityType : {dict(Counter(record.get('EntityType') for record in canonical))}")


if __name__ == "__main__":
    main()
