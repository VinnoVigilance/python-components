from pathlib import Path
import sys
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from services.adverseMediaPipeline.mediaNormalizationService import (
    MediaNormalizationService,
)


GLOBAL_CONFIG = {
    "mapping": {
        "file": "data/rules/mediaMapping.xlsx",
    },
    "pre_normalization": {
        "file": "data/rules/mediaPreNormalization.xlsx",
    },
    "post_normalization": {
        "file": "data/rules/mediaPostNormalization.xlsx",
    },
}


SOURCE_CONFIG = {
    "source_name": "NBI",
    "dataset_name": "NBI_PRESS_RELEASES",
    "dataset_category": "PRESS_RELEASE",
    "date_order": "MDY",
}


RAW_RECORD = {
    "SourceURL": (
        "https://nbi.gov.ph/"
        "sample-press-release/11014/"
    ),
    "SourceRecordId": "11014",
    "Title": "Sample NBI Press Release",
    "OriginalValue": "September 8, 2026",
    "AuthorName": "NBI",
    "BodyText": "Sample body text",
}


def main():
    service = MediaNormalizationService(
        global_config=GLOBAL_CONFIG,
        source_config=SOURCE_CONFIG,
    )

    result = service.normalize(
        RAW_RECORD
    )

    print("\n=== RAW RECORD ===")
    pprint(RAW_RECORD)

    print("\n=== NORMALIZED RECORD ===")
    pprint(result)


if __name__ == "__main__":
    main()