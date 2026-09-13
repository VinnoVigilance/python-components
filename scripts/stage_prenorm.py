"""Stage 4 - PRE-NORMALIZATION: run the real PreNormalizationEngine on the
preprocess artifact (per-source raw cleanup before mapping). DB-free.
Reads data/final/<LIST>_preprocessed.jsonl, writes data/raw/<LIST>_prenorm.jsonl.

Usage:
    python -m scripts.stage_prenorm EU-MOST-WANTED --preview
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._harness import (
    finish,
    get_config,
    load_stage_input,
    stage_parser,
    stage_prenorm,
)


def main(argv=None) -> None:
    args = stage_parser("Run PRE-NORMALIZATION on the preprocess artifact.").parse_args(argv)
    config = get_config(args.list_name)
    records = load_stage_input(args.list_name, "prenorm", args.infile)
    pre_normalized = stage_prenorm(config, records)
    finish(args.list_name, "prenorm", pre_normalized, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
