"""
Unit tests for risk/ruleMatcher.py

The matcher applies the deterministic ListScope + Rules layers to a record and
produces its RiskCategories. We build a RiskConfig directly (no Excel) and run
whole records through classify_record.
"""

import pytest

from risk.configLoader import ListScopeEntry, RiskConfig, Rule
from risk.ruleMatcher import (
    RuleMatcher,
    _match_value,
    _merge_contributions,
    load_jsonl_safe,
)

pytestmark = pytest.mark.unit


def make_config():
    return RiskConfig(
        list_scope={
            "OFAC-SDN": ListScopeEntry(
                "OFAC-SDN", True, "Sanctions", "Sanctions", "Sanctioned", 1.0
            ),
            "DNFBP": ListScopeEntry("DNFBP", False, None, None, None, None),
        },
        categories={"Sanctions": "d", "Crime": "d"},
        subcategories={
            "Sanctioned": {"parent": "Sanctions", "description": None},
            "Proliferation": {"parent": "Crime", "description": None},
        },
        indicators={},
        rules=[
            Rule("R1", 1.0, None, "Program", "exact", "NPWMD",
                 "Crime", "Proliferation", 0.9),
        ],
    )


class TestMatchValue:
    def test_exact_is_case_insensitive(self):
        assert _match_value("NPWMD", "exact", "npwmd") is True
        assert _match_value("NPWMDX", "exact", "npwmd") is False

    def test_contains(self):
        assert _match_value("has NPWMD inside", "contains", "npwmd") is True

    def test_regex(self):
        assert _match_value("NPWMD-2", "regex", r"NPWMD") is True

    def test_none_value_never_matches(self):
        assert _match_value(None, "exact", "npwmd") is False


class TestClassifyRecord:
    def test_base_label_plus_rule(self):
        record = {
            "Sources": [{"ListName": "OFAC-SDN"}],
            "Programs": [{"Program": "NPWMD"}],
        }
        result = RuleMatcher(make_config()).classify_record(record)

        labels = {(c["Category"], c["SubCategory"]) for c in result["RiskCategories"]}
        assert labels == {
            ("Sanctions", "Sanctioned"),
            ("Crime", "Proliferation"),
        }

    def test_highest_confidence_sorts_first(self):
        record = {
            "Sources": [{"ListName": "OFAC-SDN"}],
            "Programs": [{"Program": "NPWMD"}],
        }
        result = RuleMatcher(make_config()).classify_record(record)

        # ListScope base label (conf 1.0) must outrank the rule label (0.9)
        assert result["RiskCategories"][0]["Category"] == "Sanctions"

    def test_excluded_only_record_gets_empty_categories(self):
        record = {"Sources": [{"ListName": "DNFBP"}]}
        result = RuleMatcher(make_config()).classify_record(record)

        assert result["RiskCategories"] == []

    def test_list_name_hint_supplies_missing_source(self):
        # Record has no Sources[]; the pipeline passes the authoritative list.
        record = {"Programs": [{"Program": "NPWMD"}]}
        result = RuleMatcher(make_config()).classify_record(record, list_name="OFAC-SDN")

        labels = {(c["Category"], c["SubCategory"]) for c in result["RiskCategories"]}
        assert ("Sanctions", "Sanctioned") in labels

    def test_original_record_is_not_mutated(self):
        record = {"Sources": [{"ListName": "OFAC-SDN"}]}
        RuleMatcher(make_config()).classify_record(record)

        assert "RiskCategories" not in record  # worked on a deepcopy


class TestMergeContributions:
    def test_same_label_is_merged_keeping_max_confidence_and_union_of_sources(self):
        contributions = [
            {"category": "Sanctions", "subcategory": "Sanctioned", "confidence": 0.5,
             "method": "listscope", "evidence": "e1", "source": "A", "indicator": None},
            {"category": "Sanctions", "subcategory": "Sanctioned", "confidence": 0.9,
             "method": "rule", "evidence": "e2", "source": "B"},
        ]
        merged = _merge_contributions(contributions)

        assert len(merged) == 1
        assert merged[0]["Confidence"] == 0.9
        assert merged[0]["Method"] == "listscope+rule"
        assert set(merged[0]["Sources"]) == {"A", "B"}


class TestLoadJsonlSafe:
    def test_skips_blank_conflict_and_bad_lines(self, tmp_path):
        path = tmp_path / "data.jsonl"
        path.write_text(
            '{"a": 1}\n'
            "\n"                 # blank -> skipped silently
            "<<<<<<< HEAD\n"     # git conflict marker -> counted as skipped
            "{bad json\n"        # unparseable -> counted as skipped
            '{"b": 2}\n',
            encoding="utf-8",
        )

        records, skipped = load_jsonl_safe(path)

        assert records == [{"a": 1}, {"b": 2}]
        assert skipped == 2
