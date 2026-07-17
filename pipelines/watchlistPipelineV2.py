# pipelines/watchlistPipelineV2.py

from pprint import pprint
from time import perf_counter
from typing import Any
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from ingestion.downloader import interface as downloader
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistFileService


def run_watchlist_pipeline(
    watchlist_name: str,
) -> dict[str, Any]:
    """Run the implemented watchlist pipeline stages."""

    if watchlist_name not in WATCHLIST_CONFIGS:
        available_watchlists = ", ".join(
            sorted(WATCHLIST_CONFIGS)
        )

        raise ValueError(
            f"Unknown watchlist: {watchlist_name}. "
            f"Available watchlists: {available_watchlists}"
        )

    started_at = perf_counter()
    config: dict[str, Any] = WATCHLIST_CONFIGS[watchlist_name]

    source_file_path = watchlistFileService.acquire_source_file(
        config=config,
        downloader=downloader,
    )

    file_metadata = watchlistFileService.calculate_file_metadata(
        file_path=source_file_path,
    )

    lookup_values = watchlistFileService.resolve_lookup_values(
    config=config,
    )

    download_method = config["download_method"]

    duplicate_status = watchlistFileService.check_duplicate(
        source_id=lookup_values["source_id"],
        list_type_id=lookup_values["list_type_id"],
        file_hash=file_metadata["file_hash"],
    )


    result = {
        "watchlist_name": watchlist_name,
        "source_name": config["source_name"],
        "list_name": config.get(
            "list_name",
            config["source_name"],
        ),
        "source_file_path": str(source_file_path),
        "file_metadata": file_metadata,
        "lookup_values": lookup_values,
        "download_method": download_method,
        "duplicate_status": duplicate_status,
    }

    if duplicate_status == "DUPLICATE":
        result["pipeline_result"] = "SKIPPED"
        result["storage_path"] = None
        result["elapsed_seconds"] = round(
            perf_counter() - started_at,
            2,
        )

        return result

    if duplicate_status in {"FIRST_DOWNLOAD","NEW_VERSION",}:
        storage_path = (
            watchlistFileService.store_source_file(
                config=config,
                file_path=source_file_path,
            )
        )

        result["pipeline_result"] = "CONTINUE"
        result["storage_path"] = storage_path
        result["elapsed_seconds"] = round(
            perf_counter() - started_at,
            2,
        )

        return result

    raise RuntimeError(
        f"Unknown duplicate status: {duplicate_status}"
    )

    


if __name__ == "__main__":
    result = run_watchlist_pipeline(
        watchlist_name="EU-DESIGNATED-VESSELS",
    )

    pprint(result)