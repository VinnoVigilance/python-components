import logging

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
logger = logging.getLogger(__name__)


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

        # MappingEngine uses entity_type to select Media rules. The original
        # raw record is not mutated.
        record["entity_type"] = "Media"

        logger.debug(
            "Normalizing Media record. dataset=%s field_count=%s",
            self.dataset_name,
            len(record),
        )

        if self.pre_normalizer is not None:
            record = self.pre_normalizer.pre_normalize_record(
                source=self.dataset_name,
                raw_json=record,
            )

            logger.debug(
                "Media pre-normalization completed. "
                "dataset=%s field_count=%s",
                self.dataset_name,
                len(record),
            )
        else:
            logger.debug(
                "Media pre-normalization skipped. dataset=%s",
                self.dataset_name,
            )

        mapped_record = self.mapper.map_record(
            record
        )

        canonical_record = (
            self.post_normalizer.post_normalize_record(
                mapped_record
            )
        )

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

        rules = load_rules(
            mapping_file=str(mapping_file),
            source_name=self.dataset_name,
        )

        logger.debug(
            "Loaded Media mapping rules. dataset=%s file=%s count=%s",
            self.dataset_name,
            mapping_file,
            len(rules),
        )

        if not rules:
            logger.warning(
                "No Media mapping rules were loaded. dataset=%s",
                self.dataset_name,
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

        pre_df = pd.read_excel(
            pre_file
        )

        logger.debug(
            "Loaded Media pre-normalization rules. "
            "dataset=%s file=%s count=%s",
            self.dataset_name,
            pre_file,
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

        post_df = pd.read_excel(
            post_file
        )

        logger.debug(
            "Loaded Media post-normalization rules. "
            "dataset=%s file=%s count=%s",
            self.dataset_name,
            post_file,
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
