import sys
from pathlib import Path
from pprint import pprint

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from services.adverseMediaPipeline.mediaRawRecordService import (
    MediaRawRecordService,
)
from services.adverseMediaPipeline.mediaNormalizationService import (
    MediaNormalizationService,
)


CONFIG_PATH = (
    PROJECT_ROOT
    / "config"
    / "mediaSources.yaml"
)

SOURCE_KEY = "NBI_PRESS_RELEASES"


# فعلاً چند Raw Record نمونه برای تست کامل transformation flow
RAW_RECORDS = [
    {
        "SourceURL": (
            "https://nbi.gov.ph/"
            "sample-press-release/11014/"
        ),
        "SourceRecordId": "11014",
        "Title": "Sample NBI Press Release",
        "OriginalValue": "September 8, 2026",
        "AuthorName": "NBI",
        "BodyText": "Sample body text",
    },
    {
        "SourceURL": (
            "https://nbi.gov.ph/"
            "sample-press-release/11015/"
        ),
        "SourceRecordId": "11015",
        "Title": "Second Sample NBI Press Release",
        "OriginalValue": "September 7, 2026",
        "AuthorName": "NBI",
        "BodyText": "Second sample body text",
    },
    {
        "SourceURL": (
            "https://nbi.gov.ph/"
            "sample-press-release/11016/"
        ),
        "SourceRecordId": "11016",
        "Title": "Third Sample NBI Press Release",
        "OriginalValue": "September 6, 2026",
        "AuthorName": "NBI",
        "BodyText": "Third sample body text",
    },
]


def load_config():
    with CONFIG_PATH.open(
        "r",
        encoding="utf-8",
    ) as handle:
        config = yaml.safe_load(handle)

    global_config = config["global"]
    source_config = config["sources"][SOURCE_KEY]

    return global_config, source_config


def main():
    print(
        "\n=== MEDIA PIPELINE TEST ==="
    )

    (
        global_config,
        source_config,
    ) = load_config()

    print(
        "Source:",
        SOURCE_KEY,
    )

    print(
        "Input records:",
        len(RAW_RECORDS),
    )

    # ==========================================
    # 1. RAW RECORD LAYER
    # ==========================================

    raw_service = (
        MediaRawRecordService()
    )

    raw_records = raw_service.process(
        source_config=source_config,
        records=RAW_RECORDS,
    )

    print(
        "Raw/preprocessed records:",
        len(raw_records),
    )

    # ==========================================
    # 2. NORMALIZATION
    # ==========================================

    normalization_service = (
        MediaNormalizationService(
            global_config=global_config,
            source_config=source_config,
        )
    )

    normalized_records = (
        normalization_service.normalize_many(
            raw_records
        )
    )

    # ==========================================
    # 3. OUTPUT
    # ==========================================

    print(
        "\n================================"
    )

    print(
        "FINAL NORMALIZED RECORDS"
    )

    print(
        "================================"
    )

    for index, record in enumerate(
        normalized_records,
        start=1,
    ):
        print(
            f"\n--- RECORD {index} ---"
        )

        pprint(
            record,
            sort_dicts=False,
        )

    print(
        "\n================================"
    )

    print(
        "Pipeline test completed successfully."
    )

    print(
        "================================"
    )


if __name__ == "__main__":
    main()