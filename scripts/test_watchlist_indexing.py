import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(
    0,
    str(ROOT_DIR),
)


from services.watchlistPipeline.watchlistElasticsearchService import (
    run_initial_load,
)


def main() -> None:
    result = run_initial_load(
        batch_size=100,
        max_rows=None,
    )

    print(
        "\nWatchlist Elasticsearch indexing result:"
    )

    for key, value in result.items():
        print(
            f"{key}: {value}"
        )


if __name__ == "__main__":
    main()