"""
Inspect the JSON a source produces *after* preprocessing -- i.e. once the
external_id (the `unique_id` field) has been generated.

It reuses the real pipeline pieces so the output matches production exactly:
  * WATCHLIST_CONFIGS            -> the source's config (url, params, rules)
  * build_query / extract_items -> the real API paging + item extraction
  * PreProcessingEngine         -> the real preprocessing (incl. composite id)

It does NOT write any snapshot to disk and does NOT touch the database; it
only fetches, runs preprocessing in memory, and prints JSON to the console.

Usage (from the repo root, with the vv-env interpreter):

  ./vv-env/Scripts/python.exe scripts/inspect_preprocessed_records.py DMW-RECRUITMENT-AGENCIES --limit 3
  ./vv-env/Scripts/python.exe scripts/inspect_preprocessed_records.py GPPB-BLACKLISTED-ENTITIES --limit 3 --insecure

Flags:
  --limit N     stop after N records (default 3). Keep small: it prints full JSON.
  --max-pages N stop after N pages (default 1). DMW is paged; GPPB is a single call.
  --insecure    disable TLS certificate verification (needed for the GPPB host
                on some Windows machines whose cert store rejects it).
  --raw         also print the raw record (before preprocessing) for comparison.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

# Make the repo root importable when run from anywhere.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ingestion.apiCollector.pagination import (  # noqa: E402
    build_query,
    extract_items,
    should_stop,
)
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS  # noqa: E402
from transforms.preProcessingEngine import PreProcessingEngine  # noqa: E402


def fetch_records(config, max_pages, limit, insecure):
    """
    Fetch raw records the same way the real collector does.

    ``limit`` / ``max_pages`` of None mean "no cap" -- fetch every page until
    the API returns an empty page (this is the real collector's behaviour).
    Nothing is written to disk here.
    """
    api = config["api_config"]
    url = config["url"]
    pagination = api.get("pagination", {"type": "none"})
    base_params = api.get("params", {})
    items_path = api.get("items_path", "")
    headers = api.get("headers") or {"User-Agent": "Mozilla/5.0"}
    throttle = api.get("throttle_delay")

    pagination_type = pagination.get("type", "page")
    # Mirror the real collector: fetch once per param variant (merged over the
    # base params), or a single fetch when no variants are declared.
    variants = api.get("param_variants") or [{}]

    records = []
    pages_done = 0

    for variant in variants:
        params = {**base_params, **variant}
        page = pagination.get("start_page", 1)

        while True:
            query = build_query(pagination=pagination, params=params, page=page)
            resp = requests.get(
                url,
                params=query,
                headers=headers,
                timeout=40,
                verify=not insecure,
            )
            resp.raise_for_status()
            items = extract_items(resp.json(), items_path)

            if should_stop(items):
                break

            records.extend(items)
            pages_done += 1

            if limit is not None and len(records) >= limit:
                return records[:limit]
            if max_pages is not None and pages_done >= max_pages:
                return records
            if pagination_type == "none":
                break
            page += 1

            if throttle:
                time.sleep(throttle)

    return records if limit is None else records[:limit]


def describe_id_rule(config):
    """Explain which preprocessing rule builds the external_id, and how."""
    ext_path = config.get("external_id_path")
    for rule in config.get("preprocessing", []):
        if rule.get("handler") == "generate_composite_id":
            c = rule["config"]
            if c.get("output_field", "unique_id") == ext_path:
                return ext_path, c
    return ext_path, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("list_name", help="key in WATCHLIST_CONFIGS, e.g. DMW-RECRUITMENT-AGENCIES")
    ap.add_argument("--out", help="write ALL preprocessed records to this .jsonl file (one JSON object per line)")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N records (default: preview of 3 when printing, ALL when --out is set)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="stop after N pages (default: 1 when printing, ALL when --out is set)")
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--raw", action="store_true")
    args = ap.parse_args()

    if args.insecure:
        import urllib3
        urllib3.disable_warnings()

    config = WATCHLIST_CONFIGS.get(args.list_name)
    if config is None:
        sys.exit(f"Unknown list_name: {args.list_name}\nKnown: {', '.join(sorted(WATCHLIST_CONFIGS))}")

    # Caps: printing to console defaults to a small preview; writing a file
    # defaults to the whole dataset. An explicit --limit/--max-pages always wins.
    if args.out:
        limit = args.limit          # None -> all records
        max_pages = args.max_pages  # None -> all pages
    else:
        limit = args.limit if args.limit is not None else 3
        max_pages = args.max_pages if args.max_pages is not None else 1

    ext_path, id_cfg = describe_id_rule(config)
    print(f"# list_name        : {args.list_name}")
    print(f"# external_id_path : {ext_path}  (the field that holds the external id)")
    if id_cfg:
        print(f"# id handler       : generate_composite_id")
        print(f"# id fields        : {id_cfg['fields']}")
        print(f"# id prefix        : {id_cfg.get('prefix', '')!r}")
        print(f"# recipe           : SHA256( '|'.join( str(field).strip().upper() for field in id fields ) ), then 'PREFIX-' + digest")
    else:
        print(f"# id handler       : (no generate_composite_id rule feeds {ext_path})")
    print("#" + "-" * 70)

    records = fetch_records(config, max_pages, limit, args.insecure)
    processed = PreProcessingEngine().preprocess(records, config.get("preprocessing", []))

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

    for i, (raw, rec) in enumerate(zip(records, processed), 1):
        print(f"\n===== record {i} =====")
        if id_cfg:
            parts = [str(raw.get(f, "")).strip().upper() for f in id_cfg["fields"]]
            print("id inputs (field -> value used):")
            for f in id_cfg["fields"]:
                print(f"    {f} = {raw.get(f)!r}")
            print(f"raw_id string = {' | '.join(parts)}")
            print(f"{ext_path} = {rec.get(ext_path)}")
        if args.raw:
            print("--- raw (before preprocessing) ---")
            print(json.dumps(raw, indent=2, ensure_ascii=False))
        print("--- after preprocessing ---")
        print(json.dumps(rec, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
