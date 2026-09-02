"""
Test ONLY the profile-hydration step (Phase B) of the Interpol list+detail
collector, reusing an already-produced overview JSONL -- so profile fetching can
be exercised WITHOUT re-running the multi-hour fan-out planner (Phase A).

It builds the SAME task and browser transport the real pipeline uses, warms the
stealth session once, then calls the collector's own ``_fetch_profiles`` over the
overview file: for each notice it follows the detail URL and writes the profile
to ``attachments/members/{id}.json`` next to the overview. Resumable -- a profile
already on disk is skipped -- so you can Ctrl+C after a few to smoke-test, then
re-run to continue where it left off.

Usage:
    # use the default overview (the 07:49 run on 2026-08-29):
    ./vv-env/Scripts/python.exe scripts/test_profile_fetch.py
    # or point it at a specific overview .jsonl:
    ./vv-env/Scripts/python.exe scripts/test_profile_fetch.py "<path to overview .jsonl>"
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running by path or by module: put the repo root on sys.path so the
# first-party packages import either way.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from config.loggingConfig import configure_logging
from ingestion.apiCollector.collector import _build_transport, _fetch_profiles
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistFileService

LIST_NAME = "INTERPOL-RED-NOTICES"

# Default overview: the listing the 07:49 run on 2026-08-29 already produced.
DEFAULT_OVERVIEW = (
    _REPO_ROOT
    / "data/downloads/INTERPOL/INTERPOL-RED-NOTICES"
    / "year=2026/month=08/day=29"
    / "INTERPOL-RED-NOTICES_20260829_074904.jsonl"
)


def main(argv=None) -> None:
    # Send the collector's INFO logs (including the browser transport's) to the
    # terminal, so each profile fetch is visible -- same as a real run.
    configure_logging()

    argv = sys.argv[1:] if argv is None else argv
    overview_path = Path(argv[0]) if argv else DEFAULT_OVERVIEW

    if not overview_path.exists():
        raise SystemExit(f"Overview file not found: {overview_path}")

    # Build the SAME task the real pipeline builds, so the transport, detail
    # config, id path and throttle are identical to a production run.
    config = WATCHLIST_CONFIGS[LIST_NAME]
    task = watchlistFileService.build_api_task(config)

    # Profiles land next to the overview, exactly where a full run puts them:
    # .../day=NN/attachments/members/{id}.json
    members_dir = overview_path.parent / "attachments" / "members"

    print(f"List        : {LIST_NAME}")
    print(f"Overview    : {overview_path}")
    print(f"Members dir : {members_dir}")
    print(f"Transport   : {task.transport}")
    print("-" * 60)
    print(
        "Fetching profiles (warming browser session; Ctrl+C to stop -- "
        "profiles already on disk are skipped on re-run)..."
    )

    transport = _build_transport(task)

    with transport:
        saved = _fetch_profiles(task, overview_path, members_dir, transport)

    print("-" * 60)
    print(f"DONE -- saved {saved} new profiles under {members_dir}")


if __name__ == "__main__":
    main()
