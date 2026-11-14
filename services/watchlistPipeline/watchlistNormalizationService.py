from pathlib import Path
from typing import Any

import pandas as pd

from transforms.fieldMapper import (
    MappingEngine,
    load_rules,
)
from transforms.postNormalization import (
    PostNormalizationEngine,
)
from transforms.preNormalization import (
    PreNormalizationEngine,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
RULES_DIR = ROOT_DIR / "data" / "rules"


def create_normalization_engines(
    config: dict[str, Any],
) -> tuple[
    PreNormalizationEngine,
    MappingEngine,
    PostNormalizationEngine,
]:
    """Create normalization engines for one watchlist."""

    prenormalization_df = pd.read_excel(
        RULES_DIR / "preNormalization.xlsx"
    )

    source_config_df = pd.read_excel(
        RULES_DIR / "sourceConfig.xlsx"
    )

    post_normalization_df = pd.read_excel(
        RULES_DIR / "postNormalization.xlsx"
    )

    mapping_rules = load_rules(
        mapping_file=RULES_DIR / "mapping.xlsx",
        source_name=config["list_name"],
    )

    pre_normalizer = PreNormalizationEngine(
        prenormalization_df=prenormalization_df,
        source_config_df=source_config_df,
    )

    mapper = MappingEngine(
        rules=mapping_rules,
    )

    post_normalizer = PostNormalizationEngine(
        rules_df=post_normalization_df,
        config=config,
    )

    return (
        pre_normalizer,
        mapper,
        post_normalizer,
    )


def normalize_record(
    raw_record: dict[str, Any],
    config: dict[str, Any],
    pre_normalizer: PreNormalizationEngine,
    mapper: MappingEngine,
    post_normalizer: PostNormalizationEngine,
) -> dict[str, Any]:
    """Convert one Raw record into a Canonical record."""

    pre_normalized_record = (
        pre_normalizer.pre_normalize_record(
            source=config["list_name"],
            raw_json=raw_record,
        )
    )

    mapped_record = mapper.map_record(
        pre_normalized_record
    )

    canonical_record = (
        post_normalizer.post_normalize_record(
            mapped_record
        )
    )

    if not isinstance(canonical_record, dict):
        raise TypeError(
            "Canonical record must be a dictionary."
        )

    for source in canonical_record.get("Sources") or []:
        if not isinstance(source, dict):
            continue
        categories = source.get("DatasetCategory")
        if isinstance(categories, str):
            source["DatasetCategory"] = [
                category.strip() for category in categories.split(",")
                if category.strip()
            ]

    risk_category = canonical_record.get("RiskCategory")
    if risk_category is None:
        risk_category = {}
    if not isinstance(risk_category, dict):
        raise ValueError("RiskCategory must be an object.")
    risk_entries = risk_category.get("Labels")
    if risk_entries is None:
        risk_entries = []
    if not isinstance(risk_entries, list):
        raise ValueError("RiskCategory.Labels must be an array.")
    complete = []
    for entry in risk_entries:
        if not isinstance(entry, dict):
            raise ValueError("RiskCategory.Labels entries must be objects.")
        origin, value = entry.get("Type"), entry.get("Value")
        if not origin and not value:
            continue
        if not origin or not value:
            raise ValueError("RiskCategory.Labels entries require both Type and Value.")
        complete.append(entry)
    risk_category["Labels"] = complete

    canonical_record["RiskCategory"] = risk_category

    return canonical_record
