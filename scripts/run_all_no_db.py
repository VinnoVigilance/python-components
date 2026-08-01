"""
Run the DB-free transform pipeline for EVERY watchlist and report counts.

For each list in WATCHLIST_CONFIGS it finds the most recent downloaded source
file (latest year/month/day, or the config's local_path for manual lists),
runs the real parser -> preprocessing -> normalization chain, writes
data/final/<LIST>_final.jsonl, and prints how many records were parsed and
written. A list that writes 0 records is flagged so empty output is obvious.

No database, no network.

Usage:
    python scripts/run_all_no_db.py                # all lists
    python scripts/run_all_no_db.py UN-SANCTIONS DFAT   # only these
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parsing.parserFactory import create_parser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline.watchlistNormalizationService import (
    create_normalization_engines,
    normalize_record,
)
from transforms.preProcessingEngine import PreProcessingEngine

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "data" / "downloads"
FINAL = ROOT / "data" / "final"
EXT = {"xml": ".xml", "xlsx": ".xlsx", "html": ".html", "pdf": ".pdf", "csv": ".csv"}
# Never treat downloaded attachments (profile pages, images) as the source file.
SKIP_PARTS = ("attachment", "profile", "image")


def pick_file(list_name: str, cfg: dict) -> Path | None:
    """Most recent source file for a list (latest day), or its local_path."""
    local = cfg.get("local_path")
    if local and (ROOT / local).is_file():
        return ROOT / local

    ext = EXT.get(cfg.get("file_type", ""), "")
    source = cfg.get("source_name", "")
    candidates: list[Path] = []

    for base in (DOWNLOADS / source / list_name, DOWNLOADS / list_name):
        if base.is_dir():
            for p in base.rglob(f"*{ext}"):
                if p.is_file() and not any(
                    s in str(p).lower() for s in SKIP_PARTS
                ):
                    candidates.append(p)

    # Flat files like data/downloads/DFAT_2026....xlsx or data/downloads/UKSL.xml
    for pattern in (f"{list_name}*{ext}", f"*{list_name}*{ext}"):
        candidates += [p for p in DOWNLOADS.glob(pattern) if p.is_file()]

    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run_one(list_name: str) -> dict:
    cfg = WATCHLIST_CONFIGS[list_name]
    src = pick_file(list_name, cfg)
    if not src:
        return {"status": "NO FILE", "file": "-", "parsed": 0, "written": 0, "types": {}}

    # Stage 1 + 2: real parser + real preprocessing
    parsed = list(create_parser(file_type=cfg["file_type"]).parse(
        file_path=str(src), config=cfg))
    rules = deepcopy(cfg.get("preprocessing", []))
    for rule in rules:
        rc = rule.get("config", {})
        for pf in rule.get("relative_path_fields", []):
            if rc.get(pf):
                rc[pf] = str(src.parent / rc[pf])
    processed = PreProcessingEngine().preprocess(records=parsed, rules=rules)

    # Stage 3: real normalization -> JSONL
    pre, mapper, post = create_normalization_engines(cfg)
    FINAL.mkdir(parents=True, exist_ok=True)
    out = FINAL / f"{cfg['list_name']}_final.jsonl"

    types: Counter = Counter()
    written = 0
    with open(out, "w", encoding="utf-8") as fo:
        for rec in processed:
            canonical = normalize_record(rec, cfg, pre, mapper, post)
            types[canonical.get("EntityType")] += 1
            fo.write(json.dumps(canonical, ensure_ascii=False) + "\n")
            written += 1

    return {
        "status": "OK",
        "file": src.name,
        "parsed": len(parsed),
        "processed": len(processed),
        "written": written,
        "types": dict(types),
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("watchlists", nargs="*", help="specific lists; default = all")
    args = ap.parse_args(argv)
    names = args.watchlists or list(WATCHLIST_CONFIGS)

    results = []
    for name in names:
        try:
            r = run_one(name)
        except Exception as exc:  # keep going so one bad list doesn't stop the rest
            r = {"status": f"ERROR {type(exc).__name__}: {exc}",
                 "file": "-", "parsed": 0, "written": 0, "types": {}}
            traceback.print_exc()
        results.append((name, r))
        print(f"  {name:40} parsed={r['parsed']:>6}  written={r['written']:>6}  "
              f"[{r['status']}]")

    print("\n================= SUMMARY =================")
    tot_p = tot_w = 0
    for name, r in results:
        tot_p += r["parsed"]
        tot_w += r["written"]
        flag = "   <-- EMPTY OUTPUT" if (r["status"] == "OK" and r["written"] == 0) else ""
        print(f"{name:40} parsed={r['parsed']:>6}  written={r['written']:>6}  "
              f"{r.get('types', {})}{flag}")
    print(f"\nTOTAL  parsed={tot_p}   written={tot_w}   lists={len(results)}")


if __name__ == "__main__":
    main()
