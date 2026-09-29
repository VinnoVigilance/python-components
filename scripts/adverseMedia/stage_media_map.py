"""Stage 4 - MAPPING: run the real MappingEngine (mediaMapping.xlsx) on the prenorm
artifact (raw source fields -> canonical JSON). DB-free.
Reads data/raw/<DATASET>_prenorm.jsonl, writes data/raw/<DATASET>_mapped.jsonl.

Usage:
    python -m scripts.adverseMedia.stage_media_map NBI_PRESS_RELEASES --preview
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts._shared import finish, load_stage_input
from scripts.adverseMedia._media_harness import (
    _load_engines,
    get_media_config,
    media_stage_parser,
    stage_map,
)


def main(argv=None) -> None:
    args = media_stage_parser("Run MAPPING on the prenorm artifact.").parse_args(argv)
    global_config, source_config = get_media_config(args.dataset_name)
    records = load_stage_input(args.dataset_name, "map", args.infile)
    service = _load_engines(global_config, source_config)
    mapped = stage_map(service, records)
    finish(args.dataset_name, "map", mapped, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
