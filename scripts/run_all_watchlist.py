import sys
from pathlib import Path
from time import perf_counter


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from pipelines.watchlistPipeline import run_watchlist_pipeline


def main() -> None:
    watchlist_names = list(WATCHLIST_CONFIGS.keys())

    print("Watchlists to run:")
    print(watchlist_names)
    print(f"Total: {len(watchlist_names)}")

    for watchlist_name in watchlist_names:
        print(f"\nStarting: {watchlist_name}")

        started_at = perf_counter()

        try:
            run_watchlist_pipeline(
                watchlist_name=watchlist_name
            )

            elapsed = perf_counter() - started_at

            print(
                f"{watchlist_name} finished "
                f"in {elapsed:.2f} seconds"
            )

        except Exception as exc:
            elapsed = perf_counter() - started_at

            print(
                f"{watchlist_name} failed "
                f"after {elapsed:.2f} seconds: {exc}"
            )


if __name__ == "__main__":
    main()