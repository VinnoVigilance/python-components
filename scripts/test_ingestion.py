"""
Test the DOWNLOAD / INGESTION step for a single watchlist (no DB, no parsing).

It runs the SAME acquisition the real pipeline uses --
``services/watchlistPipeline/watchlistFileService.acquire_source_file()`` --
which dispatches on the list's ``download_method``:

    HTTPS / (blank)   -> ingestion.downloader        (plain file download)
    API               -> ingestion.apiCollector      (paged API snapshot)
    BYPASS            -> ingestion.bypassCollector    (stealth browser)
    Manual/local_path -> uses the already-present local file

So testing any ingestion method is just: set LIST_NAME to that list and run.
It prints which method was used and the saved file's path + size. No database,
no normalization.

There is also a dry-run PLAN mode for faceted API sources (e.g. Interpol Red
Notices). It runs ONLY the cheap "how many?" queries the fan-out planner needs
and prints the cap, the whole-dataset total, how many slices it would fetch, and
any slice that still exceeds the cap -- WITHOUT paging or fetching a single
profile. Use it to sanity-check the cap / fan-out for a fraction of a full run.

Usage:
    # set LIST_NAME in main() below, then:
    python -m scripts.test_ingestion
    python scripts/test_ingestion.py
    # or override once from the command line:
    python scripts/test_ingestion.py ATC-DESIGNATED-TERRORIST-INDIVIDUALS
    # dry-run the fan-out plan (no records fetched):
    python scripts/test_ingestion.py INTERPOL-RED-NOTICES --plan
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running by path or by module: put the repo root on sys.path so the
# first-party packages import either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.loggingConfig import configure_logging
from ingestion.apiCollector import interface as api_collector
from ingestion.downloader import interface as downloader
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistFileService


def _require_config(list_name: str) -> dict:
    """Look up a list's config or exit with the available names."""
    if list_name not in WATCHLIST_CONFIGS:
        available = ", ".join(sorted(WATCHLIST_CONFIGS))
        raise SystemExit(
            f"Unknown list: {list_name}\nAvailable lists: {available}"
        )

    return WATCHLIST_CONFIGS[list_name]


def plan_ingestion(list_name: str) -> None:
    """
    Dry-run the fan-out plan for a faceted API source: run only the "how many?"
    queries and report the cap, totals, and slice count -- no records fetched.
    """
    config = _require_config(list_name)

    if config.get("download_method") != "API":
        raise SystemExit(
            f"{list_name} is not an API source "
            f"(download_method={config.get('download_method')!r}); "
            f"--plan only applies to faceted API sources."
        )

    # Build the SAME task the real pipeline builds, so the plan shown is exactly
    # what a real run would use.
    task = watchlistFileService.build_api_task(config)

    print(f"List           : {list_name}")
    print(f"Source         : {config.get('source_name')}")
    print(f"URL            : {config.get('url', '-')}")

    if not task.faceting.get("enabled"):
        print("-" * 60)
        print(
            "No faceting enabled -- this source fetches directly, so there is "
            "no fan-out plan to show."
        )
        return

    print(f"Cap            : {task.faceting.get('cap', 160)}")
    print(f"Transport      : {task.transport}")
    print("-" * 60)
    print("Planning (only 'how many?' queries; no records fetched)...")

    plan = api_collector.plan(task)

    print("-" * 60)
    print(f"Root total (API reports): {plan.root_total:,}")
    print(f"Leaf slices to fetch    : {len(plan.leaves):,}")
    print(f"Over-cap slices         : {len(plan.unresolved):,}")

    if plan.leaves:
        print("Example slices:")
        for leaf in plan.leaves[:5]:
            print(f"   {leaf}")

    if plan.unresolved:
        print("-" * 60)
        print(
            f"🔴 {len(plan.unresolved)} slice(s) STILL exceed the cap after "
            f"all facets -- the data has outgrown the current facets. Add "
            f"another facet type to faceting.facets. Examples:"
        )
        for over in plan.unresolved[:5]:
            print(f"   {over['params']} = {over['total']:,} records")
    else:
        print("Plan OK -- every slice is within the cap.")


def test_ingestion(list_name: str) -> None:
    """Acquire the source file for one list and report the result."""
    config = _require_config(list_name)

    print(f"List           : {list_name}")
    print(f"Source         : {config.get('source_name')}")
    print(f"Download method: {config.get('download_method', '(none)')}")
    print(f"URL            : {config.get('url', '-')}")
    print("-" * 60)

    # Same acquisition the pipeline runs; it picks download / API / bypass /
    # manual based on the config's download_method.
    source_file = watchlistFileService.acquire_source_file(
        config=config,
        downloader=downloader,
    )

    size = source_file.stat().st_size
    print("-" * 60)
    print("INGESTION OK")
    print(f"Saved file     : {source_file}")
    print(f"File size      : {size:,} bytes")


def main(argv=None) -> None:
    # Send ingestion logs (including the bypass collector's) to the terminal.
    configure_logging()

    # --- set the list you want to test here ---
    LIST_NAME = "GPPB-BLACKLISTED-ENTITIES"

    # Optional one-off override from the command line, e.g.
    #   python scripts/test_ingestion.py DFAT
    #   python scripts/test_ingestion.py INTERPOL-RED-NOTICES --plan
    argv = sys.argv[1:] if argv is None else argv

    plan_mode = "--plan" in argv
    positional = [arg for arg in argv if not arg.startswith("--")]

    if positional:
        LIST_NAME = positional[0]

    if plan_mode:
        plan_ingestion(LIST_NAME)
    else:
        test_ingestion(LIST_NAME)


if __name__ == "__main__":
    main()
