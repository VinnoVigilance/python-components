from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from transforms.fieldMapper import (
    MappingEngine,
    load_rules,
)
from transforms.preNormalization import (
    PreNormalizationEngine,
)
from transforms.postNormalization import (
    PostNormalizationEngine,
)


ROOT_DIR = Path(__file__).resolve().parents[2]


class MediaNormalizationService:
    def __init__(
        self,
        global_config: dict[str, Any],
        source_config: dict[str, Any],
    ):
        self.global_config = global_config
        self.source_config = source_config

        self.dataset_name = source_config["dataset_name"]

        self.pre_normalizer = self._create_pre_normalizer()
        self.mapper = self._create_mapper()
        self.post_normalizer = self._create_post_normalizer()

    def normalize(
        self,
        raw_record: dict[str, Any],
    ) -> dict[str, Any]:

        record = deepcopy(raw_record)

        # فقط برای اینکه MappingEngine بداند این رکورد Media است.
        # raw_record اصلی تغییر نمی‌کند.
        record["entity_type"] = "Media"

        print("\n=== DEBUG NORMALIZATION ===")
        print("DATASET NAME:", self.dataset_name)
        print("ENTITY TYPE INPUT:", record.get("entity_type"))
        print("RAW RECORD KEYS:", list(record.keys()))

        if self.pre_normalizer is not None:
            record = self.pre_normalizer.pre_normalize_record(
                source=self.dataset_name,
                raw_json=record,
            )

            print(
                "AFTER PRE-NORMALIZATION KEYS:",
                list(record.keys()),
            )
        else:
            print("PRE-NORMALIZATION: SKIPPED")

        mapped_record = self.mapper.map_record(
            record
        )

        print("\n=== MAPPED RECORD ===")
        print(mapped_record)

        canonical_record = (
            self.post_normalizer.post_normalize_record(
                mapped_record
            )
        )

        print("\n=== AFTER POST-NORMALIZATION ===")
        print(canonical_record)

        if not isinstance(canonical_record, dict):
            raise TypeError(
                "Canonical Media record must be a dictionary."
            )

        return canonical_record

    def normalize_many(
        self,
        raw_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:

        return [
            self.normalize(record)
            for record in raw_records
        ]

    def _create_mapper(
        self,
    ) -> MappingEngine:

        mapping_file = self._get_rule_file(
            "mapping"
        )

        print("\n=== DEBUG MAPPING FILE ===")
        print("MAPPING FILE:", mapping_file)
        print("DATASET:", self.dataset_name)

        rules = load_rules(
            mapping_file=str(mapping_file),
            source_name=self.dataset_name,
        )

        print("RULE COUNT:", len(rules))

        for index, rule in enumerate(
            rules[:15],
            start=1,
        ):
            print(
                f"RULE {index}:",
                "entity_type=",
                getattr(rule, "entity_type", None),
                "| target_path=",
                getattr(rule, "target_path", None),
                "| source_type=",
                getattr(rule, "source_type", None),
                "| source_path=",
                getattr(rule, "source_path", None),
            )

        if not rules:
            print(
                "\nWARNING: No mapping rules were loaded "
                f"for source '{self.dataset_name}'."
            )

        return MappingEngine(
            rules=rules
        )

    def _create_pre_normalizer(
        self,
    ) -> PreNormalizationEngine | None:

        pre_file = self._get_rule_file(
            "pre_normalization"
        )

        print("\n=== DEBUG PRE-NORMALIZATION FILE ===")
        print("PRE FILE:", pre_file)

        pre_df = pd.read_excel(
            pre_file
        )

        print(
            "PRE-NORMALIZATION ROW COUNT:",
            len(pre_df),
        )

        if pre_df.empty:
            return None

        source_config_df = pd.DataFrame(
            [
                {
                    "source": self.dataset_name,
                    "entity_field": "entity_type",
                }
            ]
        )

        return PreNormalizationEngine(
            prenormalization_df=pre_df,
            source_config_df=source_config_df,
        )

    def _create_post_normalizer(
        self,
    ) -> PostNormalizationEngine:

        post_file = self._get_rule_file(
            "post_normalization"
        )

        print("\n=== DEBUG POST-NORMALIZATION FILE ===")
        print("POST FILE:", post_file)

        post_df = pd.read_excel(
            post_file
        )

        print(
            "POST-NORMALIZATION ROW COUNT:",
            len(post_df),
        )

        return PostNormalizationEngine(
            rules_df=post_df,
            config=self.source_config,
        )

    def _get_rule_file(
        self,
        section: str,
    ) -> Path:

        section_config = self.global_config.get(
            section,
            {},
        )

        file_path = section_config.get(
            "file"
        )

        if not file_path:
            raise ValueError(
                f"global.{section}.file is required."
            )

        path = Path(
            file_path
        )

        if not path.is_absolute():
            path = ROOT_DIR / path

        path = path.resolve()

        if not path.exists():
            raise FileNotFoundError(
                f"Media rule file not found: {path}"
            )

        return path