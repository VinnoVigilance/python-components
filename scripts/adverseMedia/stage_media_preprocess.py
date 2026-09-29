"""Stage 2 - PREPROCESS: run the real media preprocessing (config['preprocessing'])
on the extract artifact. This is the shape mapping sees. DB-free.
Reads data/raw/<DATASET>_extracted.jsonl, writes data/final/<DATASET>_preprocessed.jsonl.

Usage:
    python -m scripts.adverseMedia.stage_media_preprocess NBI_PRESS_RELEASES --preview
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts._shared import finish, load_stage_input
from scripts.adverseMedia._media_harness import get_media_config, media_stage_parser, stage_preprocess


def main(argv=None) -> None:
    args = media_stage_parser("Run PREPROCESSING on the extract artifact.").parse_args(argv)
    _, source_config = get_media_config(args.dataset_name)
    records = load_stage_input(args.dataset_name, "preprocess", args.infile)
    processed = stage_preprocess(source_config, records)
    finish(args.dataset_name, "preprocess", processed, out=args.out, preview=args.preview, limit=args.limit)


if __name__ == "__main__":
    main()
