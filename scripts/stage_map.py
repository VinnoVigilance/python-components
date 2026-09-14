"""Stage 5 - MAPPING: run the real MappingEngine (mapping.xlsx) on the prenorm
artifact (raw source -> canonical JSON). DB-free.
Reads data/raw/<LIST>_prenorm.jsonl, writes data/raw/<LIST>_mapped.jsonl.

Usage:
    python -m scripts.stage_map EU-MOST-WANTED --preview
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._harness import (
    finish,
    get_config,
    load_stage_input,
    stage_map,
    stage_parser,
)


def main(argv=None) -> None:
    args = stage_parser("Run MAPPING on the prenorm artifact.").parse_args(argv)
    config = get_config(args.list_name)
    records = load_stage_input(args.list_name, "map", args.infile)
    mapped = stage_map(config, records)
    finish(args.list_name, "map", mapped, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
