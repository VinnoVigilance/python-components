"""Shared dev harness: run any watchlist pipeline stage DB-free over on-disk artifacts.

The six stages mirror the real pipeline:
    ingest -> extract -> preprocess -> prenorm -> map -> postnorm

For crawler sources the spider both fetches and extracts, so ingest is fused into
extract (there is no separate re-parseable file). Every stage reads the previous
stage's artifact and writes its own, so any stage can be run on its own once the
earlier ones have produced their output. Artifacts keep the previous testing
layout: extract/prenorm/map under data/raw/, preprocess/postnorm under data/final/;
the timestamped raw source download stays under data/downloads/.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ingestion.crawler.interface import crawl
from ingestion.crawler.models import CrawlerTask
from ingestion.downloader import interface as downloader
from parsing.parserFactory import create_parser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistFileService
from services.watchlistPipeline.watchlistNormalizationService import (
    create_normalization_engines,
)
from transforms.preProcessingEngine import PreProcessingEngine

DOWNLOADS = ROOT / "data" / "downloads"
RAW_DIR = ROOT / "data" / "raw"
FINAL_DIR = ROOT / "data" / "final"

STAGES = ["ingest", "extract", "preprocess", "prenorm", "map", "postnorm"]

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

EXT = {
    "xml": ".xml",
    "xlsx": ".xlsx",
    "html": ".html",
    "pdf": ".pdf",
    "csv": ".csv",
    "json": ".json",
    "jsonl": ".jsonl",
}
SKIP_PARTS = ("attachment", "profile", "image")


def get_config(list_name: str) -> dict:
    """Look up a watchlist config or exit with the known names."""
    config = WATCHLIST_CONFIGS.get(list_name)
    if config is None:
        raise SystemExit(
            f"Unknown list: {list_name}\n"
            f"Known: {', '.join(sorted(WATCHLIST_CONFIGS))}"
        )
    return config


def is_crawler(config: dict) -> bool:
    return str(config.get("download_method", "")).upper() == "CRAWLER"


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


def _meta_path(list_name: str) -> Path:
    return RAW_DIR / f"{list_name}_meta.json"


def read_meta(list_name: str) -> dict:
    path = _meta_path(list_name)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_meta(list_name: str, **values) -> None:
    meta = read_meta(list_name)
    meta.update(
        {key: (str(value) if value is not None else None) for key, value in values.items()}
    )
    path = _meta_path(list_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


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


def find_latest_source_file(list_name: str, config: dict) -> Path | None:
    """Most recent downloaded listing file for a list (never an attachment), or its local_path."""
    local = config.get("local_path")
    if local and (ROOT / local).is_file():
        return ROOT / local

    ext = EXT.get(config.get("file_type", ""), "")
    source = config.get("source_name", "")
    candidates: list[Path] = []

    for base in (DOWNLOADS / source / list_name, DOWNLOADS / list_name):
        if base.is_dir():
            candidates += [
                p
                for p in base.rglob(f"*{ext}")
                if p.is_file() and not any(s in str(p).lower() for s in SKIP_PARTS)
            ]

    for pattern in (f"{list_name}*{ext}", f"*{list_name}*{ext}"):
        candidates += [p for p in DOWNLOADS.glob(pattern) if p.is_file()]

    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _resolve_preprocessing_rules(config: dict, source_file) -> list[dict]:
    """Resolve rule paths (e.g. attachments_dir) against the source file's folder."""
    rules = deepcopy(config.get("preprocessing", []))
    for rule in rules:
        rule_config = rule.get("config", {})
        for path_field in rule.get("relative_path_fields", []):
            relative = rule_config.get(path_field)
            if relative and source_file is not None:
                rule_config[path_field] = str(Path(source_file).parent / relative)
    return rules


def stage_ingest(config: dict) -> Path | None:
    """Acquire the source FILE (file / API / bypass). None for crawler sources."""
    if is_crawler(config):
        return None
    return Path(
        watchlistFileService.acquire_source_file(config=config, downloader=downloader)
    )


def stage_extract(config: dict, source_file=None) -> tuple[list[dict], Path | None]:
    """Parser (file) or spider (crawler / saved_html) -> raw records + the source file used."""
    extraction_method = str(config.get("extraction_method", "")).upper()

    if is_crawler(config):
        result = crawl(
            CrawlerTask(
                url=config["url"],
                source_name=config["source_name"],
                list_name=config["list_name"],
                source_config_path=str((ROOT / config["source_config"]).resolve()),
                download_dir=str(DOWNLOADS),
            )
        )
        src = Path(result.source_file_path) if result.source_file_path else None
        return list(result.records or []), src

    if extraction_method == "SAVED_HTML_SPIDER":
        if source_file:
            src = Path(source_file)
        else:
            src = Path(
                watchlistFileService.acquire_source(
                    config=config, downloader=downloader
                ).source_file_path
            )
        result = crawl(
            CrawlerTask(
                url=config["url"],
                source_name=config["source_name"],
                list_name=config["list_name"],
                source_config_path=str((ROOT / config["source_config"]).resolve()),
                source_file_path=str(src),
                download_dir=str(DOWNLOADS),
            )
        )
        return list(result.records or []), src

    src = source_file and Path(source_file) or find_latest_source_file(
        config["list_name"], config
    )
    if src is None:
        raise SystemExit(
            f"No downloaded source file for {config['list_name']}; "
            f"run: python -m scripts.stage_ingest {config['list_name']}"
        )
    parser = create_parser(file_type=config["file_type"])
    return list(parser.parse(file_path=str(src), config=config)), Path(src)


def stage_preprocess(config: dict, records: list[dict], source_file=None) -> list[dict]:
    rules = _resolve_preprocessing_rules(config, source_file)
    return PreProcessingEngine().preprocess(records=records, rules=rules)


def stage_prenorm(config: dict, records: list[dict], engines=None) -> list[dict]:
    pre_normalizer = (engines or create_normalization_engines(config))[0]
    return [
        pre_normalizer.pre_normalize_record(source=config["list_name"], raw_json=record)
        for record in records
    ]


def stage_map(config: dict, records: list[dict], engines=None) -> list[dict]:
    mapper = (engines or create_normalization_engines(config))[1]
    return [mapper.map_record(record) for record in records]


def stage_postnorm(config: dict, records: list[dict], engines=None) -> list[dict]:
    post_normalizer = (engines or create_normalization_engines(config))[2]
    canonical = [post_normalizer.post_normalize_record(record) for record in records]
    for record in canonical:
        if not isinstance(record, dict):
            raise TypeError("Canonical record must be a dictionary.")
    return canonical


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


def stage_parser(description: str) -> argparse.ArgumentParser:
    """Shared argument parser for the record-transforming stage CLIs."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("list_name", help="key in WATCHLIST_CONFIGS, e.g. EU-MOST-WANTED")
    parser.add_argument("--in", dest="infile", default=None, help="override input artifact (.jsonl)")
    parser.add_argument("--out", default=None, help="override output artifact path")
    parser.add_argument("--preview", action="store_true", help="print the first --limit records")
    parser.add_argument("--limit", type=int, default=3)
    return parser


def _emit(list_name, stage, records, quiet, preview, limit) -> None:
    if quiet:
        write_jsonl(artifact_path(list_name, stage), records)
    else:
        finish(list_name, stage, records, preview=preview, limit=limit)


def run_chain(
    list_name: str,
    source_file=None,
    stop: str = "postnorm",
    preview: bool = False,
    limit: int = 3,
    quiet: bool = False,
) -> dict:
    """Run extract -> ... -> `stop` for one list, snapshotting each stage. DB-free."""
    config = get_config(list_name)
    summary: dict = {"list_name": list_name}

    records, src = stage_extract(config, source_file=source_file)
    if not is_crawler(config):
        write_meta(list_name, source_file=src)
    _emit(list_name, "extract", records, quiet, preview, limit)
    summary["extract"] = len(records)
    if stop == "extract":
        return summary

    records = stage_preprocess(config, records, source_file=src)
    _emit(list_name, "preprocess", records, quiet, preview, limit)
    summary["preprocess"] = len(records)
    if stop == "preprocess":
        return summary

    engines = create_normalization_engines(config)

    for stage, transform in (
        ("prenorm", stage_prenorm),
        ("map", stage_map),
        ("postnorm", stage_postnorm),
    ):
        records = transform(config, records, engines=engines)
        _emit(list_name, stage, records, quiet, preview, limit)
        summary[stage] = len(records)
        if stop == stage and stage != "postnorm":
            return summary

    summary["types"] = dict(Counter(record.get("EntityType") for record in records))
    if not quiet:
        print(f"EntityType : {summary['types']}")
    return summary
