"""Backfill DBM annual English directories, oldest to newest.

Uses the configured DBM parser/preprocessing and the CIA backfill's existing
file registration, duplicate/resume, Raw and Core loader. Each complete annual
snapshot reconciles the active assignments through continuous versioning.
Database history dates remain ingestion dates, as in the CIA backfill; the
edition year is preserved in the source URL and filename.

    python -m scripts.watchlist.backfill_dbm_government_directory --dry-run
    python -m scripts.watchlist.backfill_dbm_government_directory
    python -m scripts.watchlist.backfill_dbm_government_directory --start 2022 --end 2024

After a failure, rerun with --start set to the reported year. Starting before
the latest registered edition is rejected to avoid reversing Core history.
Run without a concurrent live DBM ingestion job.
"""

import argparse
from copy import deepcopy
from datetime import date
import json
import logging
from pathlib import Path
import re
from time import sleep

from ingestion.downloader import interface as downloader
from ingestion.urlResolver.linkResolver import resolve_latest_year_page
from infrastructure.database.connection import get_db_connection
from parsing.dbm.parser import DbmDirectoryParser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from scripts.watchlist.backfill_cia_world_leaders import load_month as load_snapshot
from services.watchlistPipeline import watchlistFileService


ROOT_DIR = Path(__file__).resolve().parents[2]
DBM_KEY = "DBM_PH_GOVERNMENT_OFFICIALS_DIRECTORY"
MAX_EDITION_ATTEMPTS = 3
RETRY_SECONDS = 3
logger = logging.getLogger(__name__)


def build_edition_config(year):
    """Pin discovery to this edition without changing the live configuration."""
    config = deepcopy(WATCHLIST_CONFIGS[DBM_KEY])
    config["url_resolver"]["first_year"] = year
    config["download_filename"] = f"dbm-government-directory-{year}.pdf"
    return config


def validate_start_year(start):
    """Read-only check: never replay an older snapshot over a newer one."""
    config = WATCHLIST_CONFIGS[DBM_KEY]
    lookup = watchlistFileService.resolve_lookup_values(config)
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT file_name, list_url FROM raw.watchlist_file
                WHERE source_id = %s AND list_type_id = %s
                ORDER BY downloaded_at DESC, id DESC LIMIT 1
            """, (lookup["source_id"], lookup["list_type_id"]))
            latest = cursor.fetchone()
    if latest is None:
        return
    filename, url = latest
    match = re.fullmatch(r"dbm-government-directory-(\d{4})\.pdf", filename or "")
    if match is None:
        match = re.search(r"/(\d{4})-english-version(?:[/?#]|$)", url or "")
    if match is None:
        raise ValueError(
            "Existing DBM ingestion has no identifiable edition year. "
            "Check its source before starting historical backfill."
        )
    latest_year = int(match[1])
    if start < latest_year:
        raise ValueError(
            f"Latest registered DBM edition is {latest_year}; --start {start} "
            f"would reverse history. Resume with --start {latest_year} or later."
        )


def fetch_edition(year):
    """Resolve exactly one year; only confirmed missing pages return None."""
    config = build_edition_config(year)
    try:
        url = resolve_latest_year_page(config["url_resolver"], last_year=year)
    except FileNotFoundError:
        return None
    config.pop("url_resolver")
    config["url"] = url
    path = watchlistFileService.acquire_source_file(config, downloader)
    records = list(DbmDirectoryParser().parse(path, config))
    minimum = config["minimum_record_count"]
    if len(records) < minimum:
        raise ValueError(
            f"DBM {year}: only {len(records)} listings; expected >= {minimum}. "
            "Refusing to load a partial snapshot."
        )
    return config, records, path


def run_edition(year, fetched, *, dry_run, jsonl_dir):
    config, records, path = fetched
    if dry_run:
        jsonl_dir.mkdir(parents=True, exist_ok=True)
        output = jsonl_dir / f"{year}.jsonl"
        with output.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"status": "PARSED", "record_count": len(records), "output": str(output)}
    return load_snapshot(config, records, path)


def load_edition(year, *, dry_run, jsonl_dir):
    """Retry this edition and stop on failure before loading any later year."""
    fetched = None
    for attempt in range(1, MAX_EDITION_ATTEMPTS + 1):
        try:
            if fetched is None:
                fetched = fetch_edition(year)
            if fetched is None:
                logger.info("DBM %s: absent (skipped)", year)
                return
            result = run_edition(year, fetched, dry_run=dry_run, jsonl_dir=jsonl_dir)
            logger.info("DBM %s: %d listings -> %s", year, len(fetched[1]), result)
            return result
        except Exception as error:
            logger.exception("DBM %s: attempt %d/%d failed", year, attempt, MAX_EDITION_ATTEMPTS)
            if attempt == MAX_EDITION_ATTEMPTS:
                raise RuntimeError(
                    f"STOPPED at DBM {year}. Nothing after it was loaded. "
                    f"Fix the issue, then rerun with --start {year}."
                ) from error
            sleep(RETRY_SECONDS * attempt)


def main(argv=None):
    resolver = WATCHLIST_CONFIGS[DBM_KEY]["url_resolver"]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=int, default=resolver["first_year"], help="First edition year.")
    parser.add_argument("--end", type=int, default=date.today().year + resolver["years_ahead"],
                        help="Last year to probe (default: current year + configured lookahead).")
    parser.add_argument("--dry-run", action="store_true", help="Download and parse only; no DB/storage writes.")
    parser.add_argument("--jsonl-dir", type=Path, default=ROOT_DIR / "data/raw/dbm_historical")
    args = parser.parse_args(argv)
    if not resolver["first_year"] <= args.start <= args.end <= 9999:
        parser.error(f"Require {resolver['first_year']} <= --start <= --end <= 9999")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if not args.dry_run:
            validate_start_year(args.start)
        for year in range(args.start, args.end + 1):
            load_edition(year, dry_run=args.dry_run, jsonl_dir=args.jsonl_dir)
    except Exception as error:
        logger.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
