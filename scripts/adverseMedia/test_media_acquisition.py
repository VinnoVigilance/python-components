import argparse
import sys
from pathlib import Path

import yaml


PROJECT_ROOT = Path(
    __file__
).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from services.adverseMediaPipeline.mediaAcquisitionService import (
    MediaAcquisitionService,
)
from services.adverseMediaPipeline.mediaDiscoveryService import (
    MediaDiscoveryService,
)


MEDIA_CONFIG_PATH = (
    PROJECT_ROOT
    / "config"
    / "mediaSources.yaml"
)


def load_source_config(dataset_name: str) -> dict:

    with MEDIA_CONFIG_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:

        config = yaml.safe_load(
            file
        )

    return config[
        "sources"
    ][
        dataset_name
    ]


def cap_discovery(max_records: int) -> None:
    """Stop discovery after max_records links, for a quick dev run."""

    check_record_key = MediaDiscoveryService.check_record_key

    def capped_check_record_key(self, record_key):
        is_known, should_stop = check_record_key(self, record_key)

        if self.discovered_count >= max_records:
            self.stop_reason = "MAX_RECORDS_REACHED"
            should_stop = True

        return is_known, should_stop

    MediaDiscoveryService.check_record_key = capped_check_record_key


def main():

    parser = argparse.ArgumentParser(
        description="Run Media acquisition for one dataset.",
    )
    parser.add_argument(
        "dataset_name",
        nargs="?",
        default="NBI_PRESS_RELEASES",
        help="key under 'sources' in config/mediaSources.yaml",
    )
    parser.add_argument(
        "--mode",
        default="INCREMENTAL",
        type=str.upper,
        choices=["INITIAL", "INCREMENTAL"],
        help="INITIAL or INCREMENTAL (default: INCREMENTAL)",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="stop after this many discovered links (default: no cap)",
    )
    args = parser.parse_args()

    if args.max_records:
        cap_discovery(args.max_records)

    print(
        "\n"
        f"=== {args.dataset_name} MEDIA ACQUISITION TEST ==="
        "\n"
    )

    source_config = (
        load_source_config(
            args.dataset_name
        )
    )

    result = (
        MediaAcquisitionService()
        .acquire(
            source_config=source_config,
            mode=args.mode,
        )
    )

    print(
        "Source ID:",
        result.source_id,
    )

    print(
        "Dataset ID:",
        result.dataset_id,
    )

    print(
        "Discovered:",
        result.discovered_count,
    )

    print(
        "Stored:",
        result.stored_count,
    )

    print(
        "Duplicates:",
        result.duplicate_count,
    )

    print(
        "Failed:",
        result.failed_count,
    )

    print(
        "\n"
        "=== TEST FINISHED ==="
        "\n"
    )


if __name__ == "__main__":
    main()