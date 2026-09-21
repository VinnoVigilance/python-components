"""Stage 5 - POST-NORMALIZATION: run the real PostNormalizationEngine
(mediaPostNormalization.xlsx) on the mapping artifact -> canonical/final records. DB-free.
Reads data/raw/<DATASET>_mapped.jsonl, writes data/final/<DATASET>_final.jsonl.

Usage:
    python -m scripts.adverseMedia.stage_media_postnorm NBI_PRESS_RELEASES --preview
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
    stage_postnorm,
)


def main(argv=None) -> None:
    args = media_stage_parser("Run POST-NORMALIZATION on the mapping artifact.").parse_args(argv)
    global_config, source_config = get_media_config(args.dataset_name)
    records = load_stage_input(args.dataset_name, "postnorm", args.infile)
    service = _load_engines(global_config, source_config)
    canonical = stage_postnorm(service, records)
    finish(args.dataset_name, "postnorm", canonical, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
