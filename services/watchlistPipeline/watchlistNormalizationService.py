from pathlib import Path
from typing import Any

import pandas as pd

from transforms.fieldMapper import (
    MappingEngine,
    load_rules,
)
from transforms.dateResolver import load_approx_vocab
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

    # Load the approximate/exact vocabulary from the pre-norm sheet once and
    # carry it on config, so the date resolver reads uncertain-date words
    # (EXACT / APPROXIMATELY / BETWEEN / circa ...) from Excel, not from code.
    post_config = {
        **config,
        "approx_vocab": load_approx_vocab(prenormalization_df),
    }

    post_normalizer = PostNormalizationEngine(
        rules_df=post_normalization_df,
        config=post_config,
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

    return canonical_record