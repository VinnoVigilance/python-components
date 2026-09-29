"""Stage 3 - PRE-NORMALIZATION: stamp entity_type=Media and run the real
PreNormalizationEngine on the preprocess artifact (per-source raw cleanup). DB-free.
Reads data/final/<DATASET>_preprocessed.jsonl, writes data/raw/<DATASET>_prenorm.jsonl.

Usage:
    python -m scripts.adverseMedia.stage_media_prenorm NBI_PRESS_RELEASES --preview
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
    stage_prenorm,
)


def main(argv=None) -> None:
    args = media_stage_parser("Run PRE-NORMALIZATION on the preprocess artifact.").parse_args(argv)
    global_config, source_config = get_media_config(args.dataset_name)
    records = load_stage_input(args.dataset_name, "prenorm", args.infile)
    service = _load_engines(global_config, source_config)
    pre_normalized = stage_prenorm(service, records)
    finish(args.dataset_name, "prenorm", pre_normalized, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
