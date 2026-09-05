"""
Inspect the preprocessed JSON a source produces from its ALREADY-DOWNLOADED
file -- the shape mapping sees (e.g. {source_record_id, list, detail} for
Interpol, or the composite-id records for DMW/GPPB), produced OFFLINE from
data/downloads/ (no network, no DB).

Give it ONE list name. It:
  * picks that list's most recent downloaded source file under data/downloads/
    (or use --source-file to point at a specific one),
  * parses it with the real parser for its file_type,
  * runs the real PreProcessingEngine (the same rules as production, including
    the relative-path resolution enrich_from_attachment needs to find each
    record's detail file), and
  * prints a preview (default) or writes every preprocessed record to a .jsonl
    file with --out.

It reuses the real pipeline pieces so the output matches production exactly:
  WATCHLIST_CONFIGS / pick_file / create_parser / PreProcessingEngine.

Usage (from the repo root, with the vv-env interpreter):

  ./vv-env/Scripts/python.exe scripts/inspect_preprocessed_records.py INTERPOL-RED-NOTICES --limit 3
  ./vv-env/Scripts/python.exe scripts/inspect_preprocessed_records.py INTERPOL-RED-NOTICES --out data/final/INTERPOL-RED-NOTICES_preprocessed.jsonl
  ./vv-env/Scripts/python.exe scripts/inspect_preprocessed_records.py DMW-RECRUITMENT-AGENCIES --raw

Flags:
  --source-file PATH  parse this exact file instead of the latest download.
  --out PATH          write ALL preprocessed records to this .jsonl file
                      (one JSON object per line). Default: print a preview.
  --limit N           when printing, stop after N records (default 3). Ignored
                      with --out (which writes every record).
  --raw               also print the raw record (before preprocessing) for
                      comparison.

Note: CRAWLER sources (e.g. CFTC) have no single re-parseable download -- their
list/detail is produced by the spider from the source yaml. For those, use
scripts/run_pipeline_no_db.py (which runs the spider), not this script.
"""

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

# Make the repo root importable when run from anywhere.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from parsing.parserFactory import create_parser  # noqa: E402
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS  # noqa: E402
from scripts.run_all_no_db import pick_file  # noqa: E402  (reuse the real file finder)
from transforms.preProcessingEngine import PreProcessingEngine  # noqa: E402


def describe_id_rule(config):
    """Explain which preprocessing rule builds the external_id, and how."""
    ext_path = config.get("external_id_path")
    for rule in config.get("preprocessing", []):
        if rule.get("handler") == "generate_composite_id":
            c = rule["config"]
            if c.get("output_field", "unique_id") == ext_path:
                return ext_path, c
    return ext_path, None


def resolve_relative_paths(rules, source_file):
    """
    Resolve rule paths (e.g. attachments_dir) against the source file's folder.

    The same resolution the real pipeline does so enrich_from_attachment finds
    each record's detail file sitting next to the primary download.
    """
    resolved = deepcopy(rules)
    for rule in resolved:
        rule_config = rule.get("config", {})
        for path_field in rule.get("relative_path_fields", []):
            rel = rule_config.get(path_field)
            if rel and source_file is not None:
                rule_config[path_field] = str(Path(source_file).parent / rel)
    return resolved


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("list_name", help="key in WATCHLIST_CONFIGS, e.g. INTERPOL-RED-NOTICES")
    ap.add_argument("--source-file", default=None,
                    help="parse this exact file instead of the latest download")
    ap.add_argument("--out",
                    help="write ALL preprocessed records to this .jsonl file "
                         "(default: print a preview)")
    ap.add_argument("--limit", type=int, default=3,
                    help="when printing, stop after N records (default 3). Ignored with --out.")
    ap.add_argument("--raw", action="store_true",
                    help="also print the raw record (before preprocessing) for comparison")
    args = ap.parse_args(argv)

    config = WATCHLIST_CONFIGS.get(args.list_name)
    if config is None:
        sys.exit(f"Unknown list_name: {args.list_name}\nKnown: {', '.join(sorted(WATCHLIST_CONFIGS))}")

    list_name = config["list_name"]

    # Crawler sources have no single re-parseable file: their list/detail is
    # produced by the spider, so offline "parse the download" cannot rebuild it.
    if str(config.get("download_method", "")).upper() == "CRAWLER":
        sys.exit(
            f"'{list_name}' is a CRAWLER source: its list/detail is produced by "
            f"the spider, not a single downloaded file.\n"
            f"Use: python -m scripts.run_pipeline_no_db {args.list_name} --no-normalize"
        )

    # --- Locate the downloaded source file (offline) ---
    if args.source_file:
        source_file = Path(args.source_file)
        if not source_file.is_file():
            sys.exit(f"--source-file not found: {source_file}")
    else:
        source_file = pick_file(list_name, config)
        if source_file is None:
            sys.exit(f"No downloaded source file found for {list_name} under data/downloads/")

    ext_path, id_cfg = describe_id_rule(config)
    print(f"# list_name        : {args.list_name}")
    print(f"# source file      : {source_file}")
    print(f"# external_id_path : {ext_path}  (the field that holds the external id)")
    if id_cfg:
        print(f"# id handler       : generate_composite_id  "
              f"fields={id_cfg['fields']}  prefix={id_cfg.get('prefix', '')!r}")
    print("#" + "-" * 70)

    # --- Stage 1: PARSE the download (real parser for this file_type) ---
    parser = create_parser(file_type=config["file_type"])
    parsed = list(parser.parse(file_path=str(source_file), config=config))
    print(f"# parsed           : {len(parsed)} records")

    # --- Stage 2: PREPROCESS (real engine + relative-path resolution) ---
    rules = resolve_relative_paths(config.get("preprocessing", []), source_file)
    processed = PreProcessingEngine().preprocess(records=parsed, rules=rules)
    print(f"# preprocessed     : {len(processed)} records")

    # --- Write the whole file, or print a preview ---
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in processed:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with_id = sum(1 for rec in processed if rec.get(ext_path))
        print(f"wrote {len(processed)} records -> {out_path}")
        print(f"records carrying {ext_path}: {with_id}/{len(processed)}")
        return

    for i, rec in enumerate(processed[: args.limit], 1):
        print(f"\n===== record {i} =====")
        if args.raw and i <= len(parsed):
            print("--- raw (before preprocessing) ---")
            print(json.dumps(parsed[i - 1], indent=2, ensure_ascii=False))
        print("--- after preprocessing ---")
        print(json.dumps(rec, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
