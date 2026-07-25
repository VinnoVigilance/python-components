"""
Unit tests for transforms/postNormalization.py

Post-normalization runs a set of rule-driven handlers over each canonical
record: filling dependent fields, normalising dates, deduplicating arrays, and
computing the search-support fields. Each handler is a pure transform on an
entity dict, so we test them directly, plus the engine end-to-end from a small
rules DataFrame.
"""

import pandas as pd
import pytest

from transforms.postNormalization import (
    PostNormalizationEngine,
    _split_array_path,
    date_normalization_handler,
    deduplicate_all_arrays_handler,
    empty_dependency_handler,
    search_enrich_handler,
)

pytestmark = pytest.mark.unit


def test_split_array_path():
    assert _split_array_path("Names[].Name") == ("Names", "Name")


class TestSearchEnrichHandler:
    def test_normalize_writes_computed_field(self):
        entity = {"Names": [{"Name": "Frank Müller"}]}
        rule = {
            "condition_path": "Names[].Name",
            "target_path": "Names[].Normalized_Name",
            "action": "NORMALIZE",
        }
        search_enrich_handler(entity, rule)

        assert entity["Names"][0]["Normalized_Name"] == "frank muller"

    def test_if_empty_leaves_existing_value_untouched(self):
        entity = {"Names": [{"Name": "Frank", "Language": "English"}]}
        rule = {
            "condition_path": "Names[].Name",
            "target_path": "Names[].Language",
            "action": "LANGUAGE",
            "condition": "IF_EMPTY",
        }
        search_enrich_handler(entity, rule)

        # Language was already provided by the source -> not overwritten
        assert entity["Names"][0]["Language"] == "English"


class TestDateNormalizationHandler:
    def test_resolves_date_rows(self):
        entity = {"Dates": [{"Year": "1980", "Month": "01", "Day": "15"}]}
        date_normalization_handler(entity, {"condition_path": "Dates"}, {"date_order": "DMY"})

        assert entity["Dates"][0]["FullDate"] == "1980-01-15"

    def test_missing_date_order_raises(self):
        entity = {"Dates": [{"Year": "1980"}]}
        with pytest.raises(ValueError, match="date_order"):
            date_normalization_handler(entity, {"condition_path": "Dates"}, {})


class TestDeduplicateAllArraysHandler:
    def test_removes_duplicate_array_items(self):
        entity = {"Names": [{"Name": "A"}, {"Name": "A"}, {"Name": "B"}]}
        deduplicate_all_arrays_handler(entity, {})

        assert entity["Names"] == [{"Name": "A"}, {"Name": "B"}]


class TestEmptyDependencyHandler:
    def test_fills_target_only_where_source_is_empty(self):
        entity = {"Names": [{"Name": ""}, {"Name": "Bob"}]}
        rule = {
            "condition_path": "Names[].Name",
            "target_path": "Names[].Type",
            "value": "primary",
        }
        empty_dependency_handler(entity, rule)

        assert entity["Names"][0]["Type"] == "primary"
        assert "Type" not in entity["Names"][1]


class TestEngineEndToEnd:
    def test_runs_rules_and_does_not_mutate_input(self):
        rules_df = pd.DataFrame([{
            "priority": 1,
            "rule_type": "SEARCH_ENRICH",
            "condition_path": "Names[].Name",
            "target_path": "Names[].Normalized_Name",
            "action": "NORMALIZE",
            "condition": "",
        }])
        engine = PostNormalizationEngine(rules_df, config={})

        record = {"Names": [{"Name": "Frank Müller"}]}
        result = engine.post_normalize_record(record)

        assert result["Names"][0]["Normalized_Name"] == "frank muller"
        # original untouched (engine deepcopies)
        assert "Normalized_Name" not in record["Names"][0]
