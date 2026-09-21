"""Generic DB-free harness IO shared by the watchlist and adverse-media dev harnesses.

Pure on-disk artifact plumbing with no pipeline or config knowledge: the two-folder
artifact layout (data/raw/ + data/final/), JSONL read/write, per-stage artifact paths,
and the stage-output reporter. scripts/watchlist/_harness.py and
scripts/adverseMedia/_media_harness.py both build on these.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DOWNLOADS = ROOT / "data" / "downloads"
RAW_DIR = ROOT / "data" / "raw"
FINAL_DIR = ROOT / "data" / "final"

ARTIFACTS = {
    "extract": RAW_DIR / "{list}_extracted.jsonl",
    "preprocess": FINAL_DIR / "{list}_preprocessed.jsonl",
    "prenorm": RAW_DIR / "{list}_prenorm.jsonl",
    "map": RAW_DIR / "{list}_mapped.jsonl",
    "postnorm": FINAL_DIR / "{list}_final.jsonl",
}

PREVIOUS_STAGE = {
    "preprocess": "extract",
    "prenorm": "preprocess",
    "map": "prenorm",
    "postnorm": "map",
}


def artifact_path(list_name: str, stage: str) -> Path:
    return Path(str(ARTIFACTS[stage]).format(list=list_name))


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_stage_input(list_name: str, stage: str, override: str | None = None) -> list[dict]:
    """Records feeding `stage`: an explicit --in file, else the previous stage's artifact."""
    if override:
        return read_jsonl(Path(override))
    previous = PREVIOUS_STAGE.get(stage)
    if previous is None:
        raise SystemExit(f"Stage '{stage}' has no default input; pass --in.")
    path = artifact_path(list_name, previous)
    if not path.is_file():
        raise SystemExit(f"Missing input {path}. Run the earlier stage first.")
    return read_jsonl(path)


def finish(list_name, stage, records, *, out=None, preview=False, limit=3) -> Path:
    """Write a stage's output artifact, report the count, optionally preview records."""
    out_path = Path(out) if out else artifact_path(list_name, stage)
    write_jsonl(out_path, records)
    print(f"{stage:10} : {len(records):>6} records -> {out_path}")
    if preview:
        for index, record in enumerate(records[:limit], 1):
            print(f"\n===== {stage} record {index} =====")
            print(json.dumps(record, ensure_ascii=False, indent=2))
    return out_path


def emit(list_name, stage, records, quiet, preview, limit) -> None:
    """Quiet: persist the artifact only. Otherwise write + report (+ optional preview)."""
    if quiet:
        write_jsonl(artifact_path(list_name, stage), records)
    else:
        finish(list_name, stage, records, preview=preview, limit=limit)
