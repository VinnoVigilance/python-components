"""Shared dev harness: run any Adverse Media pipeline stage DB-free over on-disk artifacts.

Mirror of scripts/watchlist/_harness.py for the Adverse Media pipeline. Five stages mirror the
real media flow (with no DB / SeaweedFS step):

    extract -> preprocess -> prenorm -> map -> postnorm

extract crawls the live source with a stubbed, DB-free discovery service capped at
--max-records articles (enough to cross a listing page and exercise pagination). It
saves each article's HTML under data/downloads/ and the extracted source fields to
data/raw/<DATASET>_extracted.jsonl. The other stages reuse the real media services
and normalization engines. Artifacts share the watchlist layout: extract/prenorm/map
under data/raw/, preprocess/postnorm under data/final/.

DB-free because only discovery.check_record_key touches the DB in the real pipeline
(the stub replaces it) and the source/dataset lookups in acquire() are bypassed.
"""
from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ingestion.crawler.interface import crawl
from ingestion.crawler.models import CrawlerTask
from scripts._shared import DOWNLOADS, emit
from services.adverseMediaPipeline.mediaIdentityService import MediaIdentityService
from services.adverseMediaPipeline.mediaNormalizationService import (
    MediaNormalizationService,
)
from services.adverseMediaPipeline.mediaRawRecordService import MediaRawRecordService

MEDIA_CONFIG_PATH = ROOT / "config" / "mediaSources.yaml"

STAGES = ["extract", "preprocess", "prenorm", "map", "postnorm"]
DEFAULT_MAX_RECORDS = None


def get_media_config(dataset_name: str) -> tuple[dict, dict]:
    """Return (global_config, source_config) for a dataset, or exit with the known keys."""
    with MEDIA_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    sources = config.get("sources", {}) if isinstance(config, dict) else {}
    source_config = sources.get(dataset_name)
    if source_config is None:
        raise SystemExit(
            f"Unknown media dataset: {dataset_name}\n"
            f"Known: {', '.join(sorted(sources))}"
        )
    return config.get("global", {}), source_config


def acquisition_type(source_config: dict) -> str:
    return str(source_config.get("acquisition", {}).get("type", "")).strip().lower()


class _NoDbDiscoveryService:
    """DB-free stand-in for MediaDiscoveryService: nothing is known, stop after a cap."""

    def __init__(self, source_config: dict, max_records: int):
        self.source_config = source_config
        self.max_records = max_records
        self.discovered = 0

    def build_record_key(self, record: dict) -> str:
        return MediaIdentityService.generate_record_key(
            source_config=self.source_config, record=record
        )

    def check_record_key(self, record_key: str) -> tuple[bool, bool]:
        self.discovered += 1
        should_stop = (
            self.max_records is not None
            and self.discovered >= self.max_records
        )
        return False, should_stop


def _load_engines(global_config: dict, source_config: dict) -> MediaNormalizationService:
    """Build the media normalization engines, silencing the service's debug prints."""
    with redirect_stdout(io.StringIO()):
        return MediaNormalizationService(
            global_config=global_config,
            source_config=source_config,
        )


def stage_extract(source_config: dict, max_records: int) -> list[dict]:
    """Crawl the source DB-free (capped at max_records) -> the extracted source records."""
    a_type = acquisition_type(source_config)
    if a_type != "crawler":
        raise SystemExit(
            f"The media harness extract stage supports crawler sources only; "
            f"'{source_config.get('dataset_name')}' is type={a_type or 'unknown'}."
        )

    discovery = _NoDbDiscoveryService(source_config, max_records=max_records)
    task = CrawlerTask(
        url=source_config["url"],
        source_name=source_config["source_name"],
        list_name=source_config["dataset_name"],
        source_config=source_config,
        fetch_strategy="direct",
        download_dir=str(DOWNLOADS),
    )
    result = crawl(task=task, discovery_service=discovery)
    return [
        record["extracted"]
        for record in (result.records or [])
        if record.get("extracted")
    ]


def stage_preprocess(source_config: dict, records: list[dict]) -> list[dict]:
    """Run the real media preprocessing (config['preprocessing']) on the extract records."""
    return MediaRawRecordService().process(
        source_config=source_config,
        records=records,
    )


def stage_prenorm(service: MediaNormalizationService, records: list[dict]) -> list[dict]:
    """Stamp entity_type=Media, then run pre-normalization if the source has rules."""
    pre_normalized = []
    for record in records:
        staged = deepcopy(record)
        staged["entity_type"] = "Media"
        if service.pre_normalizer is not None:
            staged = service.pre_normalizer.pre_normalize_record(
                source=service.dataset_name,
                raw_json=staged,
            )
        pre_normalized.append(staged)
    return pre_normalized


def stage_map(service: MediaNormalizationService, records: list[dict]) -> list[dict]:
    """Run the real mapping engine (raw source fields -> canonical JSON)."""
    return [service.mapper.map_record(deepcopy(record)) for record in records]


def stage_postnorm(service: MediaNormalizationService, records: list[dict]) -> list[dict]:
    """Run the real post-normalization engine -> canonical/final media records."""
    canonical = [
        service.post_normalizer.post_normalize_record(deepcopy(record))
        for record in records
    ]
    for record in canonical:
        if not isinstance(record, dict):
            raise TypeError("Canonical media record must be a dictionary.")
    return canonical


def media_stage_parser(description: str) -> argparse.ArgumentParser:
    """Shared argument parser for the record-transforming media stage CLIs."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("dataset_name", help="key under 'sources' in config/mediaSources.yaml")
    parser.add_argument("--in", dest="infile", default=None, help="override input artifact (.jsonl)")
    parser.add_argument("--out", default=None, help="override output artifact path")
    parser.add_argument("--preview", action="store_true", help="print the first --limit records")
    parser.add_argument("--limit", type=int, default=3)
    return parser


def run_chain(
    dataset_name: str,
    stop: str = "postnorm",
    max_records: int = DEFAULT_MAX_RECORDS,
    preview: bool = False,
    limit: int = 3,
    quiet: bool = False,
) -> dict:
    """Run extract -> ... -> `stop` for one media dataset, snapshotting each stage. DB-free."""
    global_config, source_config = get_media_config(dataset_name)
    summary: dict = {"dataset": dataset_name}

    records = stage_extract(source_config, max_records=max_records)
    emit(dataset_name, "extract", records, quiet, preview, limit)
    summary["extract"] = len(records)
    if stop == "extract":
        return summary

    records = stage_preprocess(source_config, records)
    emit(dataset_name, "preprocess", records, quiet, preview, limit)
    summary["preprocess"] = len(records)
    if stop == "preprocess":
        return summary

    service = _load_engines(global_config, source_config)

    for stage, transform in (
        ("prenorm", stage_prenorm),
        ("map", stage_map),
        ("postnorm", stage_postnorm),
    ):
        records = transform(service, records)
        emit(dataset_name, stage, records, quiet, preview, limit)
        summary[stage] = len(records)
        if stop == stage and stage != "postnorm":
            return summary

    return summary
