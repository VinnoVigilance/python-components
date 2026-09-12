import sys
from pathlib import Path

import yaml


PROJECT_ROOT = Path(
    __file__
).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


from services.adverseMediaPipeline.mediaAcquisitionService import (
    MediaAcquisitionService,
)


MEDIA_CONFIG_PATH = (
    PROJECT_ROOT
    / "config"
    / "mediaSources.yaml"
)


def load_nbi_config() -> dict:

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
        "NBI_PRESS_RELEASES"
    ]


def main():

    print(
        "\n"
        "=== NBI MEDIA ACQUISITION TEST ==="
        "\n"
    )

    source_config = (
        load_nbi_config()
    )

    result = (
        MediaAcquisitionService()
        .acquire(
            source_config=source_config
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