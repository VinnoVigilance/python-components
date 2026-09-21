"""One-time backfill of the CIA World Leaders historical monthly PDFs.

Loads every monthly "Chiefs of State and Cabinet Members" directory
(January 2001 -> present) into the watchlist pipeline, in chronological
order, so that ``continuous`` versioning chains each seat oldest -> newest:
the newest month wins, every prior holder is retained as history, and seats
that disappear are tombstoned. Parsing lives here because the directory
layout is CIA-specific and one-time; the DB load reuses the watchlist
pipeline services (which read the DB connection from ``.env``).

Two era formats are handled and chosen by year:
  * 2001-2013  "dot-leader":  ``Position .......... Name``
  * 2014-onward "columnar":   ``Position`` and ``Name`` in fixed x-columns,
                              with a per-country ``Last Updated:`` line.

Usage:
  python -m scripts.watchlist.backfill_cia_world_leaders --dry-run          # parse only
  python -m scripts.watchlist.backfill_cia_world_leaders                    # parse + DB load
  python -m scripts.watchlist.backfill_cia_world_leaders --start 2013-01 --end 2013-03
"""

import argparse
import json
import logging
import re
import sys
from copy import deepcopy
from pathlib import Path
from statistics import median
from time import perf_counter, sleep
from typing import Any, Optional

import pdfplumber
import requests


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import (
    watchlistCoreService,
    watchlistFileService,
    watchlistRawService,
)


logger = logging.getLogger(__name__)

CIA_KEY = "CIA-WORLD-LEADERS-HISTORICAL"
BASE_URL = (
    "https://www.cia.gov/resources/world-leaders/static/historical-data/"
    "{year}/{month}{year}ChiefsDirectory.pdf"
)
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DOWNLOAD_DIR = ROOT_DIR / "data" / "downloads" / "CIA" / "historical_pdfs"

MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3
DOT_LEADER_MIN_LINES = 20
HEADER_MIN_SIZE = 9.6
MIN_HEADER_SIZE_ARTIFACT = 4.0
DOT_LEADER_RE = re.compile(r"\.{2,}")
LAST_UPDATED_RE = re.compile(r"Last\s+Updated:\s*(.+)", re.IGNORECASE)
PAGE_FOOTER_RE = re.compile(r"^(Page\s+\d+\s+of\s+\d+|\d+)$", re.IGNORECASE)
CONTINUED_RE = re.compile(r"\s*\(continued\)\s*$", re.IGNORECASE)
ALPHA_PREFIX_RE = re.compile(r"^[A-Z]\s+(?=[A-Z])")
SECTION_STOPWORDS = {
    "PREFACE", "ABBREVIATIONS", "CONTENTS", "TABLE OF CONTENTS",
    "INTRODUCTION", "NOTE", "NOTES", "INDEX", "APPENDIX",
    "CHIEFS OF STATE AND", "A DIRECTORY",
}
STOP_SECTIONS = {"INDEX", "NAME INDEX", "ALPHABETIC NAME INDEX"}


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------
def edition_url(year: int, month: str) -> str:
    return BASE_URL.format(year=year, month=month)


def download_edition(year: int, month: str, dest_dir: Path) -> Optional[Path]:
    """Fetch one monthly PDF; return its path, or None if that month is absent."""

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{month}{year}ChiefsDirectory.pdf"

    if dest.exists() and dest.stat().st_size > 10000:
        return dest

    last_error: Any = None

    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            response = requests.get(
                edition_url(year, month),
                headers={"User-Agent": USER_AGENT},
                timeout=60,
            )
        except requests.exceptions.RequestException as error:
            last_error = error
            sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        if response.status_code == 404:
            return None

        if response.status_code != 200:
            last_error = f"HTTP {response.status_code}"
            sleep(RETRY_BACKOFF_SECONDS * attempt)
            continue

        if "pdf" not in response.headers.get("Content-Type", "").lower():
            return None

        dest.write_bytes(response.content)
        return dest

    raise RuntimeError(
        f"Failed to download {month} {year} after "
        f"{MAX_DOWNLOAD_ATTEMPTS} attempts: {last_error}"
    )


# --------------------------------------------------------------------------
# PDF line model
# --------------------------------------------------------------------------
class Line:
    """One visual text line with the font signals we classify on."""

    def __init__(self, words: list[dict[str, Any]]) -> None:
        ordered = sorted(words, key=lambda w: w["x0"])
        self.words = [(w["text"], w["x0"]) for w in ordered]
        self.text = " ".join(w["text"] for w in ordered).strip()
        self.x0 = min(w["x0"] for w in ordered)
        self.size = max(float(w.get("size", 0)) for w in ordered)
        fonts = " ".join(w.get("fontname", "") for w in ordered)
        self.bold = "Bold" in fonts
        self.italic = "Italic" in fonts or "Oblique" in fonts


def _iter_page_lines(page: "pdfplumber.page.Page") -> list[Line]:
    words = page.extract_words(
        extra_attrs=["fontname", "size"],
        use_text_flow=False,
    )

    if not words:
        return []

    tops = sorted({round(w["top"]) for w in words})
    gaps = [b - a for a, b in zip(tops, tops[1:]) if b - a > 0]
    pitch = median(gaps) if gaps else 11.0
    tolerance = max(pitch * 0.6, 3.0)

    rows: list[list[dict[str, Any]]] = []
    row_top = None

    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if row_top is None or word["top"] - row_top > tolerance:
            rows.append([])
            row_top = word["top"]
        rows[-1].append(word)

    return [Line(row) for row in rows]


def _is_header(line: Line) -> bool:
    if line.size < HEADER_MIN_SIZE:
        return False

    cleaned = _clean_country(line.text)

    if not cleaned or len(cleaned) > 40:
        return False

    return cleaned.upper() not in SECTION_STOPWORDS


def _clean_country(text: str) -> str:
    text = CONTINUED_RE.sub("", text)
    text = ALPHA_PREFIX_RE.sub("", text)
    return text.strip()


def _is_content_page(lines: list[Line]) -> bool:
    has_header = any(_is_header(line) for line in lines)
    body_lines = sum(1 for line in lines if 8.0 <= line.size <= 9.5)
    return has_header and body_lines >= 5


def _is_dot_leader_format(lines: list[Line]) -> bool:
    """Detect the dot-leader layout from the file. The format switched from
    dot-leader to columnar mid-2013, so the year is not a reliable signal."""

    dot_lines = sum(1 for line in lines if DOT_LEADER_RE.search(line.text))
    return dot_lines >= DOT_LEADER_MIN_LINES


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
def parse_pdf(path: Path) -> list[dict[str, Any]]:
    """Parse one edition into per-country records shaped like the HTML extract."""

    with pdfplumber.open(path) as pdf:
        pages_lines = [_iter_page_lines(page) for page in pdf.pages]

    started = False
    content: list[Line] = []

    for lines in pages_lines:
        if not started:
            if _is_content_page(lines):
                started = True
            else:
                continue
        content.extend(lines)

    if _is_dot_leader_format(content):
        countries = _parse_dot_leader(content)
    else:
        countries = _parse_columnar(content, pages_lines)

    return [
        {
            "detail": {
                "country": c["country"],
                "last_updated": c["last_updated"],
                "note": c["note"] or None,
                "leaders": c["leaders"],
            }
        }
        for c in countries
        if c["leaders"] or c["note"]
    ]


def _new_country(name: str) -> dict[str, Any]:
    return {"country": name, "last_updated": None, "note": "", "leaders": []}


def _resolve_header(
    text: str,
    current: Optional[dict[str, Any]],
    countries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return the country for a header line, reusing it on ``(continued)`` pages."""

    name = _clean_country(text)

    if current is not None and name == current["country"]:
        return current

    country = _new_country(name)
    countries.append(country)
    return country


def _append_note(country: dict[str, Any], text: str) -> None:
    country["note"] = (country["note"] + " " + text).strip() if country["note"] else text


POSITION_PREFIXES = {
    "min", "min.-del", "sec", "sec.-gen", "dep", "dir", "gov", "governor",
    "amb", "ambassador", "chmn", "chairman", "pres", "president", "prime",
    "head", "vice", "first", "second", "deputy", "asst", "actg", "acting",
    "permanent", "chief", "council", "attorney", "state", "national",
    "adm", "gen", "lt", "maj", "col", "brig", "capt", "cdr", "cdte", "del",
    "eng", "solicitor", "speaker", "chancellor", "premier", "counselor",
}


def _starts_like_position(text: str) -> bool:
    first = text.split()[0].rstrip(".,").lower() if text.split() else ""
    return first in POSITION_PREFIXES


def _looks_like_note(text: str) -> bool:
    """A country note is a parenthetical aside or a full sentence."""

    if text.startswith("("):
        return True

    return (
        text.endswith(".")
        and len(text.split()) >= 5
        and not _starts_like_position(text)
    )


def _is_stop_section(text: str) -> bool:
    upper = _clean_country(text).upper()
    return "NAME INDEX" in upper or upper in STOP_SECTIONS


def _parse_dot_leader(lines: list[Line]) -> list[dict[str, Any]]:
    countries: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    position_buffer = ""
    just_emitted = False

    for line in lines:
        text = line.text

        if not text or PAGE_FOOTER_RE.match(text) or line.size < MIN_HEADER_SIZE_ARTIFACT:
            continue

        if _is_header(line):
            if _is_stop_section(text):
                break
            current = _resolve_header(text, current, countries)
            position_buffer = ""
            just_emitted = False
            continue

        if current is None:
            continue

        match = DOT_LEADER_RE.search(text)

        if match:
            position = text[: match.start()].strip()
            name = text[match.end():].strip() or None

            if position_buffer:
                position = f"{position_buffer} {position}".strip()
                position_buffer = ""

            current["leaders"].append({"position": position, "name": name})
            just_emitted = True
            continue

        if line.italic or _looks_like_note(text):
            _append_note(current, text)
        elif just_emitted and len(text.split()) <= 4 and current["leaders"]:
            previous = current["leaders"][-1]
            previous["name"] = (
                f'{previous["name"]} {text}'.strip() if previous["name"] else text
            )
        else:
            position_buffer = f"{position_buffer} {text}".strip()

        just_emitted = False

    return countries


def _detect_name_column_x(pages_lines: list[list[Line]], width_hint: float = 612.0) -> float:
    counts: dict[int, int] = {}

    for lines in pages_lines:
        for line in lines:
            for _, x0 in line.words:
                if x0 > width_hint * 0.45:
                    key = int(round(x0))
                    counts[key] = counts.get(key, 0) + 1

    if not counts:
        return width_hint * 0.55

    return float(max(counts, key=counts.get))


def _parse_columnar(
    lines: list[Line],
    pages_lines: list[list[Line]],
) -> list[dict[str, Any]]:
    name_x = _detect_name_column_x(pages_lines)
    split_x = name_x - 15.0

    countries: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    position_buffer = ""

    def flush_vacant() -> None:
        nonlocal position_buffer
        if current is not None and position_buffer:
            current["leaders"].append({"position": position_buffer, "name": None})
        position_buffer = ""

    for line in lines:
        text = line.text

        if not text or PAGE_FOOTER_RE.match(text) or line.size < MIN_HEADER_SIZE_ARTIFACT:
            continue

        if _is_header(line):
            flush_vacant()
            if _is_stop_section(text):
                break
            current = _resolve_header(text, current, countries)
            continue

        if current is None:
            continue

        last_updated = LAST_UPDATED_RE.search(text)

        if last_updated:
            current["last_updated"] = last_updated.group(1).strip()
            continue

        position_words = [t for t, x0 in line.words if x0 < split_x]
        name_words = [t for t, x0 in line.words if x0 >= split_x]

        if not name_words:
            fragment = " ".join(position_words).strip()
            if not fragment:
                continue
            if line.italic or _looks_like_note(fragment):
                _append_note(current, fragment)
            elif _starts_like_position(fragment):
                flush_vacant()
                position_buffer = fragment
            else:
                position_buffer = f"{position_buffer} {fragment}".strip()
            continue

        position = " ".join(position_words).strip()

        if position_buffer:
            position = f"{position_buffer} {position}".strip()
            position_buffer = ""

        name = " ".join(name_words).strip() or None
        current["leaders"].append({"position": position, "name": name})

    flush_vacant()
    return countries


# --------------------------------------------------------------------------
# DB load (mirrors run_watchlist_pipeline, injecting parsed records)
# --------------------------------------------------------------------------
def build_month_config() -> dict[str, Any]:
    config = deepcopy(WATCHLIST_CONFIGS[CIA_KEY])
    config["download_method"] = "Manual"
    config["file_type"] = "pdf"

    for key in ("extraction_method", "bypass_config", "source_config", "attachments"):
        config.pop(key, None)

    return config


def load_month(
    base_config: dict[str, Any],
    records: list[dict[str, Any]],
    pdf_path: Path,
) -> dict[str, Any]:
    """Register one month as a watchlist file and run raw + core processing."""

    config = deepcopy(base_config)
    config["local_path"] = str(pdf_path)

    file_metadata = watchlistFileService.calculate_file_metadata(file_path=pdf_path)
    lookup = watchlistFileService.resolve_lookup_values(config=config)
    source_id = lookup["source_id"]
    list_type_id = lookup["list_type_id"]

    duplicate = watchlistFileService.check_duplicate(
        source_id=source_id,
        list_type_id=list_type_id,
        file_hash=file_metadata["file_hash"],
    )
    status = duplicate["duplicate_status"]

    if status == "DUPLICATE_COMPLETED":
        return {"status": "SKIPPED_DUPLICATE"}

    file_version = watchlistFileService.determine_file_version(
        config=config,
        duplicate_status=status,
        source_id=source_id,
        list_type_id=list_type_id,
    )

    storage_path = watchlistFileService.store_source_file(
        config=config,
        file_path=pdf_path,
    )

    watchlist_file_id = watchlistFileService.insert_watchlist_file(
        config=config,
        file_metadata=file_metadata,
        source_id=source_id,
        list_type_id=list_type_id,
        storage_path=storage_path,
        file_version=file_version,
    )

    raw_result = watchlistRawService.process_records(
        records=records,
        file_path=pdf_path,
        config=config,
        watchlist_file_id=watchlist_file_id,
    )

    core_result = watchlistCoreService.process_watchlist_file(
        watchlist_file_id=watchlist_file_id,
        source_id=source_id,
        list_type_id=list_type_id,
        config=config,
    )

    return {
        "status": "LOADED",
        "watchlist_file_id": watchlist_file_id,
        "raw_record_count": raw_result["raw_record_count"],
        "new": core_result["new_count"],
        "updated": core_result["updated_count"],
        "deleted": core_result["deleted_count"],
        "skipped": core_result["skipped_count"],
    }


# --------------------------------------------------------------------------
# Enumeration + driver
# --------------------------------------------------------------------------
def parse_year_month(value: str) -> tuple[int, int]:
    year, month = value.split("-")
    return int(year), int(month)


def iter_editions(start: tuple[int, int], end: tuple[int, int]):
    (start_year, start_month), (end_year, end_month) = start, end
    year, month = start_year, start_month

    while (year, month) <= (end_year, end_month):
        yield year, MONTHS[month - 1]
        month += 1
        if month > 12:
            month, year = 1, year + 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill CIA World Leaders history.")
    parser.add_argument("--start", default="2001-01", help="First edition, YYYY-MM.")
    parser.add_argument("--end", default="2021-12", help="Last edition, YYYY-MM.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse only; write per-month JSONL, do not touch the DB.",
    )
    parser.add_argument(
        "--jsonl-dir",
        default=str(ROOT_DIR / "data" / "raw" / "cia_historical"),
        help="Where --dry-run writes per-month JSONL.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    start = parse_year_month(args.start)
    end = parse_year_month(args.end)

    base_config = None if args.dry_run else build_month_config()
    jsonl_dir = Path(args.jsonl_dir)

    if args.dry_run:
        jsonl_dir.mkdir(parents=True, exist_ok=True)

    for year, month in iter_editions(start, end):
        started_at = perf_counter()
        pdf_path = download_edition(year, month, DOWNLOAD_DIR)

        if pdf_path is None:
            logger.info("%s %s: absent (skipped)", month, year)
            continue

        records = parse_pdf(pdf_path)
        seat_count = sum(len(r["detail"]["leaders"]) for r in records)

        if args.dry_run:
            out = jsonl_dir / f"{year}-{month}.jsonl"
            with out.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            logger.info(
                "%s %s: %d countries, %d seats -> %s (%.1fs)",
                month, year, len(records), seat_count, out.name,
                perf_counter() - started_at,
            )
            continue

        result = load_month(base_config, records, pdf_path)
        logger.info(
            "%s %s: %d countries, %d seats -> %s (%.1fs)",
            month, year, len(records), seat_count, result,
            perf_counter() - started_at,
        )


if __name__ == "__main__":
    main()
