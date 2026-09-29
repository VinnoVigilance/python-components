"""Run the DB-free transform chain for EVERY watchlist and report counts.

For each list it runs the shared harness chain (extract -> ... -> postnorm),
writes data/raw/ + data/final/, and prints extracted/written counts. A list that writes
0 records is flagged. Errors are caught so one bad list doesn't stop the rest.

Usage:
    python -m scripts.watchlist.run_all_no_db                 # all lists
    python -m scripts.watchlist.run_all_no_db UN-SANCTIONS DFAT   # only these
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from scripts.watchlist._harness import run_chain


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="DB-free transform chain for many lists.")
    parser.add_argument("watchlists", nargs="*", help="specific lists; default = all")
    args = parser.parse_args(argv)
    names = args.watchlists or list(WATCHLIST_CONFIGS)

    results = []
    for name in names:
        try:
            summary = run_chain(name, quiet=True)
            status = "OK"
        except Exception as exc:
            summary = {}
            status = f"ERROR {type(exc).__name__}: {exc}"
            traceback.print_exc()
        results.append((name, status, summary))
        extracted = summary.get("extract", 0)
        written = summary.get("postnorm", 0)
        print(f"  {name:40} extract={extracted:>6}  written={written:>6}  [{status}]")

    print("\n================= SUMMARY =================")
    total_extract = total_written = 0
    for name, status, summary in results:
        extracted = summary.get("extract", 0)
        written = summary.get("postnorm", 0)
        total_extract += extracted
        total_written += written
        flag = "   <-- EMPTY OUTPUT" if status == "OK" and written == 0 else ""
        print(
            f"{name:40} extract={extracted:>6}  written={written:>6}  "
            f"{summary.get('types', {})}{flag}"
        )
    print(f"\nTOTAL  extract={total_extract}   written={total_written}   lists={len(results)}")


if __name__ == "__main__":
    main()
