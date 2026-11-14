"""Check the canonical risk-label contract and the installed IDB rules."""

from pathlib import Path

import pandas as pd
import pytest

from intelligence.risk.watchlistRiskCategory.lookupRules import RiskMappingError, record_rule_labels
from transforms.fieldMapper import MappingEngine, load_rules
from transforms.preNormalization import PreNormalizationEngine


RULES = Path(__file__).resolve().parents[3] / "data" / "rules"
TAXONOMY = {name: {"display_name": name} for name in ("Regulatory", "Crime-Fraud", "Crime-Corruption")}


@pytest.mark.parametrize("entity_type", ["Individual", "Entity"])
def test_idb_practices_are_split_before_mapping(entity_type):
    source = "IDB_SANCTIONED_ENTITIES"
    pre = PreNormalizationEngine(pd.read_excel(RULES / "preNormalization.xlsx"),
                                 pd.read_excel(RULES / "sourceConfig.xlsx"))
    raw = {"entity_type": entity_type, "source_record_id": "example", "Title": "Example",
           "Prohibited Practice": "Fraud, Corruption", "Source": "IDB"}
    prepared = pre.pre_normalize_record(source, raw)
    assert prepared["prohibitedPractices"] == [{"practice": "Fraud"}, {"practice": "Corruption"}]
    record = MappingEngine(load_rules(RULES / "mapping.xlsx", source)).map_record(prepared)
    labels = record_rule_labels(record, TAXONOMY)
    assert labels == {"Regulatory": "mapped list risk category",
                      "Crime-Fraud": "mapped record risk category",
                      "Crime-Corruption": "mapped record risk category"}
    additional = {(item["Type"], item["Value"]) for item in record["additionalInfo"]}
    assert {("Prohibited Practice", "Fraud"), ("Prohibited Practice", "Corruption"),
            ("Sanction Source", "IDB")} <= additional


def test_empty_labels_return_no_assertions():
    assert record_rule_labels({"RiskCategory": {"Labels": []}}, TAXONOMY) == {}


@pytest.mark.parametrize("record, field", [
    ({}, "RiskCategory"),
    ({"RiskCategory": {"Labels": "Regulatory"}}, "RiskCategory.Labels"),
    ({"RiskCategory": {"Labels": ["Regulatory"]}}, "RiskCategory.Labels[0]"),
    ({"RiskCategory": {"Labels": [{"Type": "Other", "Value": "Regulatory"}]}}, "RiskCategory.Labels[0].Type"),
    ({"RiskCategory": {"Labels": [{"Type": "List", "Value": "Unknown"}]}}, "RiskCategory.Labels[0].Value"),
])
def test_invalid_risk_labels_are_rejected(record, field):
    with pytest.raises(RiskMappingError) as error:
        record_rule_labels(record, TAXONOMY)
    assert error.value.field == field
