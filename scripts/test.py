"""
Run the real Watchlist Pipeline without Database or SeaweedFS.

Flow:
    Acquisition
        -> Downloader / APICollector / Crawler / BypassCollector

    Extraction
        -> ParserFactory / GenericSpider / SavedHtmlSpider

    Preprocessing
        -> data/raw/<LIST_NAME>_raw.jsonl

    PreNormalization + Mapping + PostNormalization
        -> data/final/<LIST_NAME>_final.jsonl

Skipped:
    - Database lookup
    - Duplicate checking
    - Database Raw insertion
    - Core database synchronization
    - SeaweedFS upload
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


from config.loggingConfig import configure_logging
from ingestion.crawler.interface import crawl
from ingestion.crawler.models import CrawlerTask
from ingestion.downloader import interface as downloader
from parsing.parserFactory import create_parser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import (
    watchlistFileService,
)
from services.watchlistPipeline.watchlistNormalizationService import (
    create_normalization_engines,
    normalize_record,
)
from transforms.preProcessingEngine import (
    PreProcessingEngine,
)


DOWNLOAD_DIR = (
    ROOT_DIR
    / "data"
    / "downloads"
)

RAW_DIR = (
    ROOT_DIR
    / "data"
    / "raw"
)

FINAL_DIR = (
    ROOT_DIR
    / "data"
    / "final"
)


def acquire_source(
    config: dict[str, Any],
    source_file: str | None,
):
    """
    Run the real acquisition stage.

    If --source-file is provided, acquisition is skipped
    and the existing local file is used.
    """

    if source_file:
        source_path = Path(
            source_file
        )

        if not source_path.is_absolute():
            source_path = (
                ROOT_DIR
                / source_path
            )

        source_path = (
            source_path.resolve()
        )

        if not source_path.is_file():
            raise FileNotFoundError(
                "Provided source file "
                "was not found: "
                f"{source_path}"
            )

        return (
            watchlistFileService
            .AcquisitionResult(
                source_file_path=source_path,
            )
        )

    return (
        watchlistFileService
        .acquire_source(
            config=config,
            downloader=downloader,
        )
    )


def extract_records(
    acquisition,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Extract records using the same extraction mechanism
    used by the real pipeline.
    """

    # CRAWLER acquisition can already return records.
    if acquisition.records is not None:
        return list(
            acquisition.records
        )

    extraction_method = str(
        config.get(
            "extraction_method",
            "",
        )
    ).strip().upper()

    # HTML acquired by BypassCollector must be
    # parsed by SavedHtmlSpider.
    if (
        extraction_method
        == "SAVED_HTML_SPIDER"
    ):
        source_config = config.get(
            "source_config"
        )

        if not source_config:
            raise ValueError(
                "SAVED_HTML_SPIDER source "
                "must define 'source_config'."
            )

        source_config_path = Path(
            source_config
        )

        if (
            not source_config_path
            .is_absolute()
        ):
            source_config_path = (
                ROOT_DIR
                / source_config_path
            )

        source_config_path = (
            source_config_path.resolve()
        )

        if not source_config_path.is_file():
            raise FileNotFoundError(
                "Crawler source config "
                "was not found: "
                f"{source_config_path}"
            )

        crawl_result = crawl(
            CrawlerTask(
                url=config["url"],
                source_name=(
                    config["source_name"]
                ),
                list_name=config.get(
                    "list_name",
                    config["source_name"],
                ),
                source_config_path=str(
                    source_config_path
                ),
                source_file_path=str(
                    acquisition
                    .source_file_path
                ),
                download_dir=str(
                    DOWNLOAD_DIR
                ),
            )
        )

        return list(
            crawl_result.records
        )

    # Normal files use the existing ParserFactory.
    parser = create_parser(
        file_type=config["file_type"]
    )

    return list(
        parser.parse(
            file_path=(
                acquisition
                .source_file_path
            ),
            config=config,
        )
    )


def preprocess_records(
    records: list[dict[str, Any]],
    source_file_path: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Run the exact preprocessing logic used by
    watchlistRawService.process_records().
    """

    preprocessing_rules = deepcopy(
        config.get(
            "preprocessing",
            [],
        )
    )

    for rule in preprocessing_rules:
        rule_config = rule.get(
            "config",
            {},
        )

        for path_field in rule.get(
            "relative_path_fields",
            [],
        ):
            relative_path = (
                rule_config.get(
                    path_field
                )
            )

            if relative_path:
                rule_config[
                    path_field
                ] = str(
                    source_file_path.parent
                    / relative_path
                )

    processed_records = (
        PreProcessingEngine()
        .preprocess(
            records=records,
            rules=preprocessing_rules,
        )
    )

    return list(
        processed_records
    )


def validate_raw_records(
    records: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    """
    Run validations equivalent to the checks
    performed before Raw insertion.
    """

    if not records:
        raise ValueError(
            "Extraction and preprocessing "
            "returned no records."
        )

    minimum_record_count = (
        config.get(
            "minimum_record_count"
        )
    )

    if (
        minimum_record_count is not None
        and len(records)
        < int(minimum_record_count)
    ):
        raise ValueError(
            f"Only {len(records)} records "
            "were produced; minimum expected "
            "count is "
            f"{minimum_record_count}."
        )

    external_id_path = config[
        "external_id_path"
    ]

    for record_index, record in enumerate(
        records,
        start=1,
    ):
        external_id = str(
            record.get(
                external_id_path,
                "",
            )
        ).strip()

        if not external_id:
            raise ValueError(
                "External ID was not found "
                "for record number "
                f"{record_index}. "
                "Expected field: "
                f"{external_id_path}"
            )


def write_jsonl(
    records,
    output_path: Path,
) -> int:
    """
    Write one JSON object per line.
    """

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    written_count = 0

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as output_file:
        for record in records:
            output_file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
            )

            output_file.write("\n")
            written_count += 1

    return written_count


def run_pipeline_no_db(
    watchlist_name: str,
    source_file: str | None = None,
    raw_output: str | None = None,
    final_output: str | None = None,
) -> dict[str, Any]:
    """
    Run acquisition, extraction, preprocessing
    and normalization without DB or SeaweedFS.
    """

    if (
        watchlist_name
        not in WATCHLIST_CONFIGS
    ):
        available_watchlists = ", ".join(
            sorted(
                WATCHLIST_CONFIGS
            )
        )

        raise ValueError(
            f"Unknown watchlist: "
            f"{watchlist_name}. "
            "Available watchlists: "
            f"{available_watchlists}"
        )

    started_at = perf_counter()

    config = WATCHLIST_CONFIGS[
        watchlist_name
    ]

    list_name = config.get(
        "list_name",
        watchlist_name,
    )

    print(
        f"Watchlist     : "
        f"{watchlist_name}"
    )

    print(
        f"Source        : "
        f"{config['source_name']}"
    )

    print(
        f"Download mode : "
        f"{config.get('download_method')}"
    )

    print(
        "Database      : SKIPPED"
    )

    print(
        "SeaweedFS     : SKIPPED"
    )

    # -----------------------------------------
    # Stage 1: Acquisition
    # -----------------------------------------

    acquisition = acquire_source(
        config=config,
        source_file=source_file,
    )

    source_file_path = Path(
        acquisition.source_file_path
    ).resolve()

    file_metadata = (
        watchlistFileService
        .calculate_file_metadata(
            file_path=source_file_path
        )
    )

    print(
        f"Source file   : "
        f"{source_file_path}"
    )

    print(
        f"Source size   : "
        f"{file_metadata['file_size']:,} "
        "bytes"
    )

    print(
        f"Source hash   : "
        f"{file_metadata['file_hash']}"
    )

    # -----------------------------------------
    # Stage 2: Extraction
    # -----------------------------------------

    parsed_records = extract_records(
        acquisition=acquisition,
        config=config,
    )

    print(
        f"Parsed        : "
        f"{len(parsed_records):,}"
    )

    # -----------------------------------------
    # Stage 3: Preprocessing
    # -----------------------------------------

    raw_records = preprocess_records(
        records=parsed_records,
        source_file_path=(
            source_file_path
        ),
        config=config,
    )

    validate_raw_records(
        records=raw_records,
        config=config,
    )

    print(
        f"Preprocessed  : "
        f"{len(raw_records):,}"
    )

    # -----------------------------------------
    # Stage 4: Raw JSONL
    # -----------------------------------------

    if raw_output:
        raw_path = Path(
            raw_output
        )
    else:
        raw_path = (
            RAW_DIR
            / f"{list_name}_raw.jsonl"
        )

    if not raw_path.is_absolute():
        raw_path = (
            ROOT_DIR
            / raw_path
        )

    raw_path = raw_path.resolve()

    raw_record_count = write_jsonl(
        records=raw_records,
        output_path=raw_path,
    )

    print(
        f"Raw JSONL     : "
        f"{raw_path}"
    )

    # -----------------------------------------
    # Stage 5: Normalization and Mapping
    # -----------------------------------------

    (
        pre_normalizer,
        mapper,
        post_normalizer,
    ) = create_normalization_engines(
        config
    )

    if final_output:
        final_path = Path(
            final_output
        )
    else:
        final_path = (
            FINAL_DIR
            / f"{list_name}_final.jsonl"
        )

    if not final_path.is_absolute():
        final_path = (
            ROOT_DIR
            / final_path
        )

    final_path = final_path.resolve()

    entity_type_counts: Counter = (
        Counter()
    )

    def normalized_records():
        for raw_record in raw_records:
            canonical_record = (
                normalize_record(
                    raw_record=(
                        raw_record
                    ),
                    config=config,
                    pre_normalizer=(
                        pre_normalizer
                    ),
                    mapper=mapper,
                    post_normalizer=(
                        post_normalizer
                    ),
                )
            )

            entity_type = (
                canonical_record.get(
                    "EntityType"
                )
            )

            entity_type_counts[
                entity_type
            ] += 1

            yield canonical_record

    final_record_count = write_jsonl(
        records=normalized_records(),
        output_path=final_path,
    )

    print(
        f"Final JSONL   : "
        f"{final_path}"
    )

    print(
        f"Final records : "
        f"{final_record_count:,}"
    )

    print(
        f"Entity types  : "
        f"{dict(entity_type_counts)}"
    )

    elapsed_seconds = (
        perf_counter()
        - started_at
    )

    print(
        f"Elapsed       : "
        f"{elapsed_seconds:.2f}s"
    )

    return {
        "watchlist_name": (
            watchlist_name
        ),
        "source_name": (
            config["source_name"]
        ),
        "list_name": list_name,
        "source_file_path": str(
            source_file_path
        ),
        "raw_output_path": str(
            raw_path
        ),
        "final_output_path": str(
            final_path
        ),
        "parsed_record_count": len(
            parsed_records
        ),
        "raw_record_count": (
            raw_record_count
        ),
        "final_record_count": (
            final_record_count
        ),
        "entity_type_counts": dict(
            entity_type_counts
        ),
        "elapsed_seconds": round(
            elapsed_seconds,
            2,
        ),
    }


def main() -> None:
    # Ensures relative download paths are always
    # created under the project root.
    os.chdir(
        ROOT_DIR
    )

    configure_logging()

    parser = argparse.ArgumentParser(
        description=(
            "Run the real watchlist acquisition, "
            "extraction, preprocessing and "
            "normalization components without "
            "Database or SeaweedFS."
        )
    )

    parser.add_argument(
        "watchlist_name",
        help=(
            "Watchlist key from "
            "WATCHLIST_CONFIGS"
        ),
    )

    parser.add_argument(
        "--source-file",
        help=(
            "Use an existing local source "
            "file and skip acquisition."
        ),
    )

    parser.add_argument(
        "--raw-output",
        help=(
            "Optional custom Raw JSONL "
            "output path."
        ),
    )

    parser.add_argument(
        "--final-output",
        help=(
            "Optional custom Final JSONL "
            "output path."
        ),
    )

    args = parser.parse_args()

    run_pipeline_no_db(
        watchlist_name=(
            args.watchlist_name
        ),
        source_file=(
            args.source_file
        ),
        raw_output=(
            args.raw_output
        ),
        final_output=(
            args.final_output
        ),
    )


def main() -> None:
    """
    Configure the test run here and execute
    this file directly.
    """

    os.chdir(
        ROOT_DIR
    )

    configure_logging()

    # نام لیست موردنظر را اینجا وارد کن.
    WATCHLIST_NAME = (
        "PH-HOUSE-MEMBERS"
    )

    # None یعنی acquisition واقعی اجرا شود:
    # Downloader / API / Crawler / Bypass
    SOURCE_FILE = None

    # مسیرهای خروجی پیش‌فرض:
    #
    # data/raw/<LIST_NAME>_raw.jsonl
    # data/final/<LIST_NAME>_final.jsonl
    RAW_OUTPUT = None
    FINAL_OUTPUT = None

    result = run_pipeline_no_db(
        watchlist_name=(
            WATCHLIST_NAME
        ),
        source_file=(
            SOURCE_FILE
        ),
        raw_output=(
            RAW_OUTPUT
        ),
        final_output=(
            FINAL_OUTPUT
        ),
    )

    print(
        "\nPipeline result:"
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()