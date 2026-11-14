"""Validate the canonical risk shape after the normalization pipeline."""

import pytest

from services.watchlistPipeline.watchlistNormalizationService import normalize_record


class Identity:
    def pre_normalize_record(self, source, raw_json):
        return raw_json

    def map_record(self, record):
        return record

    def post_normalize_record(self, record):
        return record


def normalize(record):
    engine = Identity()
    return normalize_record(record, {"list_name": "TEST"}, engine, engine, engine)


def test_missing_risk_category_gets_empty_labels():
    assert normalize({})["RiskCategory"] == {"Labels": []}


def test_empty_entries_are_removed_and_false_is_preserved():
    record = {"RiskCategory": {"RiskModelEnabled": False, "Labels": [
        {}, {"Type": "List", "Value": "Regulatory"},
    ]}}
    assert normalize(record)["RiskCategory"] == {"RiskModelEnabled": False, "Labels": [
        {"Type": "List", "Value": "Regulatory"},
    ]}


@pytest.mark.parametrize("risk", [
    "invalid", {"Labels": "invalid"}, {"Labels": ["invalid"]},
    {"Labels": [{"Type": "List"}]}, {"Labels": [{"Value": "Regulatory"}]},
])
def test_malformed_risk_data_is_rejected(risk):
    with pytest.raises(ValueError):
        normalize({"RiskCategory": risk})
