"""
Run the full watchlist transform pipeline WITHOUT a database.

This chains the pipeline's own real stages -- the same parser, preprocessing
engine, and normalization engines that services/watchlistPipeline uses -- and
writes the canonical output to data/final/<LIST_NAME>_final.jsonl.

The only thing it always skips is the DB handoff (raw-layer insert + core-layer
upsert). It has two modes, chosen automatically from the source's
download_method:
  * file (default): reads the most recent already-downloaded source file under
    data/downloads/ and parses it by file_type -- fully offline.
  * crawler (CRAWLER sources, e.g. CFTC): the records are produced by the
    generic crawler from the source yaml (listing + per-entry detail pages),
    which has no single re-parseable file, so this mode runs the spider. It
    still skips the DB, but unlike file mode it does hit the network.

Usage:
    python -m scripts.run_pipeline_no_db EU-FINANCIAL-SANCTIONS
    python -m scripts.run_pipeline_no_db EU-FINANCIAL-SANCTIONS --source-file <path>
    python -m scripts.run_pipeline_no_db CFTC-RED-LIST   # crawler mode
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

# Allow running by path (`python scripts/run_pipeline_no_db.py`) as well as
# by module (`python -m scripts.run_pipeline_no_db`): put the repo root on
# sys.path so first-party packages import either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingestion.crawler.interface import crawl
from ingestion.crawler.models import CrawlerTask
from parsing.parserFactory import create_parser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline.watchlistNormalizationService import (
    create_normalization_engines,
    normalize_record,
)
from transforms.preProcessingEngine import PreProcessingEngine

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / "data" / "downloads"
FINAL_DIR = ROOT / "data" / "final"


def find_latest_source_file(list_name: str, source_name: str) -> Path:
    """Pick the most recently modified downloaded file for this list."""
    candidates: list[Path] = []
    for base in (DOWNLOADS / source_name / list_name, DOWNLOADS / list_name):
        if base.exists():
            candidates += [p for p in base.rglob("*") if p.is_file()]
    if not candidates:
        raise FileNotFoundError(
            f"No downloaded source file found for {list_name} under {DOWNLOADS}"
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("watchlist", help="e.g. EU-FINANCIAL-SANCTIONS")
    ap.add_argument("--source-file", default=None, help="override the input file (file mode only)")
    args = ap.parse_args(argv)

    config = WATCHLIST_CONFIGS[args.watchlist]
    list_name = config["list_name"]
    download_method = str(config.get("download_method", "")).upper()

    # --- Stage 1: ACQUIRE + PARSE (two DB-less modes) ---
    # crawler sources (e.g. CFTC) have no single re-parseable file: their
    # extraction lives in the source yaml (listing + per-entry detail pages), so
    # the generic crawler produces the records itself. Everything else reads the
    # latest already-downloaded file and parses it by file_type, no network.
    if download_method == "CRAWLER":
        source_config = config.get("source_config")
        if not source_config:
            raise ValueError(
                f"Crawler source '{list_name}' must define 'source_config'."
            )
        print("Mode        : crawler (runs the spider, still DB-less)")
        task = CrawlerTask(
            url=config["url"],
            source_name=config["source_name"],
            list_name=list_name,
            source_config_path=source_config,
            download_dir=str(DOWNLOADS),
        )
        crawl_result = crawl(task)
        parsed_records = list(crawl_result.records or [])
        source_file = (
            Path(crawl_result.source_file_path)
            if crawl_result.source_file_path
            else None
        )
        print(f"Source file : {source_file}")
    else:
        print("Mode        : file (parses latest download, offline)")
        source_file = (
            Path(args.source_file)
            if args.source_file
            else find_latest_source_file(list_name, config["source_name"])
        )
        print(f"Source file : {source_file}")
        parser = create_parser(file_type=config["file_type"])
        parsed_records = list(parser.parse(file_path=source_file, config=config))

    print(f"Parsed      : {len(parsed_records)} records")

    # --- Stage 2: PREPROCESS (real engine) ---
    preprocessing_rules = deepcopy(config.get("preprocessing", []))
    for rule in preprocessing_rules:
        rule_config = rule.get("config", {})
        for path_field in rule.get("relative_path_fields", []):
            rel = rule_config.get(path_field)
            if rel and source_file is not None:
                rule_config[path_field] = str(source_file.parent / rel)
    processed_records = PreProcessingEngine().preprocess(
        records=parsed_records, rules=preprocessing_rules
    )
    print(f"Preprocessed: {len(processed_records)} records")

    # --- Stage 3: NORMALIZE (real engines) -> JSONL instead of DB ---
    pre, mapper, post = create_normalization_engines(config)
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FINAL_DIR / f"{list_name}_final.jsonl"

    counts: Counter = Counter()
    n = 0
    with open(out_path, "w", encoding="utf-8") as fout:
        for rec in processed_records:
            canonical = normalize_record(rec, config, pre, mapper, post)
            counts[canonical.get("EntityType")] += 1
            fout.write(json.dumps(canonical, ensure_ascii=False) + "\n")
            n += 1

    print(f"Wrote       : {n} canonical records -> {out_path}")
    print(f"EntityType  : {dict(counts)}")


if __name__ == "__main__":
    # Quick way to run from an editor / a plain `python run_pipeline_no_db.py`:
    # set the list name here and run the file. A name passed on the command line
    # still wins (e.g. `python -m scripts.run_pipeline_no_db DMW-RECRUITMENT-AGENCIES`).
    WATCHLIST = "CFTC-RED-LIST"

    main() if len(sys.argv) > 1 else main([WATCHLIST])
