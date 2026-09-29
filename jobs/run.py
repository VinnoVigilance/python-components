"""Numbered terminal menu for running the watchlist and Adverse Media jobs by hand.

Run with:

    python -m jobs.run
"""

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


def _ask(question: str, options: list[str], allow_many: bool = False) -> list[int]:
    """Print numbered options and return the chosen indexes (0-based)."""

    print(f"\n{question}")
    for number, label in enumerate(options, start=1):
        print(f"  {number}) {label}")

    hint = "number(s), e.g. 1 or 2,5" if allow_many else "number"

    while True:
        answer = input(f"Enter {hint}: ").replace(" ", "")
        try:
            picks = [int(part) - 1 for part in answer.split(",") if part]
        except ValueError:
            picks = []
        if picks and all(0 <= pick < len(options) for pick in picks) and (
            allow_many or len(picks) == 1
        ):
            return picks
        print("Please enter a number from the list.")


def _choose_sources(kind_label: str, names: list[str], pending: list[str]) -> tuple[list[str] | None, bool]:
    """Return (chosen names or None for all, retry_only)."""

    options = [f"ALL {kind_label} ({len(names)})", "Choose some from the list"]
    if pending:
        options.append(f"Only retry leftovers ({len(pending)}: {', '.join(pending)})")

    pick = _ask(f"Which {kind_label}?", options)[0]

    if pick == 0:
        return None, False
    if pick == 2:
        return None, True

    chosen = _ask(f"Pick {kind_label}:", names, allow_many=True)
    return [names[index] for index in chosen], False


def _run_watchlists() -> dict:
    from jobs.watchlistPiplineJob import run_watchlist_job
    from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
    from repositories import jobStateRepository

    names = list(WATCHLIST_CONFIGS)
    chosen, retry_only = _choose_sources(
        "watchlists", names, jobStateRepository.list_state_names("watchlist")
    )
    print("\nWatchlists have no INITIAL/INCREMENTAL: each run downloads the full list and compares.")

    return run_watchlist_job(
        configs={name: WATCHLIST_CONFIGS[name] for name in chosen} if chosen else None,
        run_now=True,
        retry_only=retry_only,
    )


def _run_media() -> dict:
    from jobs.mediaPipelineJob import run_media_job
    from pipelines.mediaPipeline import load_media_config
    from repositories import jobStateRepository

    names = [
        name
        for name, config in load_media_config().get("sources", {}).items()
        if config.get("enabled", True)
    ]
    chosen, retry_only = _choose_sources(
        "media sources", names, jobStateRepository.list_state_names("media")
    )

    mode = "INCREMENTAL"
    if not retry_only:
        modes = ["INCREMENTAL - only new items", "INITIAL - walk the whole source"]
        mode = ("INCREMENTAL", "INITIAL")[_ask("Which mode?", modes)[0]]

    return run_media_job(
        mode=mode,
        dataset_names=chosen,
        retry_only=retry_only,
    )


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT_DIR / ".env")
    except ImportError:
        pass

    pick = _ask("What do you want to run?", ["Watchlists", "Adverse media"])[0]

    try:
        result = _run_watchlists() if pick == 0 else _run_media()
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130

    print(f"Status: {result.get('status')}")
    print(f"Logs:   {result['log_path']}")
    print(f"        {result['log_path'].replace('.job.log', '.full.log')}  (every line, readable)")
    if result.get("pending_csv_path"):
        print(f"Still pending (open in Excel): {result['pending_csv_path']}")

    return 1 if result.get("failed") or result.get("retry", {}).get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
