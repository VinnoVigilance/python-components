"""
Unit tests for risk/configLoader.py

The loader reads the risk-classification workbook into a validated RiskConfig.
We build RiskConfig objects directly (no Excel needed) to test the cleaning
helpers, the convenience accessors, and the cross-reference validation.
"""

import math

import pytest

from risk.configLoader import (
    ListScopeEntry,
    RiskConfig,
    Rule,
    _norm,
    _norm_float,
    _validate,
)

pytestmark = pytest.mark.unit


def make_config():
    """A small, internally-consistent config used across the tests."""
    return RiskConfig(
        list_scope={
            "OFAC-SDN": ListScopeEntry(
                list_name="OFAC-SDN",
                included=True,
                nature="Sanctions",
                default_category="Sanctions",
                default_subcategory="Sanctioned",
                confidence=1.0,
            ),
            "DNFBP": ListScopeEntry(
                list_name="DNFBP",
                included=False,
                nature=None,
                default_category=None,
                default_subcategory=None,
                confidence=None,
            ),
        },
        categories={"Sanctions": "desc", "Crime": "desc"},
        subcategories={
            "Sanctioned": {"parent": "Sanctions", "description": "sd"},
            "Proliferation": {"parent": "Crime", "description": "pd"},
        },
        indicators={},
        rules=[
            Rule(
                rule_id="R1",
                priority=1.0,
                applies_to_list=None,
                match_field="Program",
                match_type="exact",
                match_value="NPWMD",
                add_category="Crime",
                add_subcategory="Proliferation",
                confidence=0.9,
            )
        ],
    )


class TestNorm:
    def test_none_and_nan_become_none(self):
        assert _norm(None) is None
        assert _norm(math.nan) is None

    def test_empty_becomes_none(self):
        assert _norm("   ") is None

    def test_collapses_nbsp_and_whitespace(self):
        assert _norm("Organized\xa0 Crime") == "Organized Crime"

    def test_norm_float(self):
        assert _norm_float("0.9") == 0.9
        assert _norm_float("abc") is None
        assert _norm_float(None) is None


class TestAccessors:
    def test_is_included(self):
        cfg = make_config()
        assert cfg.is_included("OFAC-SDN") is True
        assert cfg.is_included("DNFBP") is False
        assert cfg.is_included("Nonexistent") is False

    def test_included_lists(self):
        assert make_config().included_lists() == ["OFAC-SDN"]

    def test_default_label_for_included(self):
        assert make_config().default_label("OFAC-SDN") == (
            "Sanctions", "Sanctioned", 1.0,
        )

    def test_default_label_for_excluded_is_none(self):
        assert make_config().default_label("DNFBP") is None

    def test_allowed_labels(self):
        assert make_config().allowed_labels() == {
            ("Sanctions", "Sanctioned"),
            ("Crime", "Proliferation"),
        }

    def test_rule_applies_to_all_lists_when_unscoped(self):
        rule = make_config().rules[0]
        assert rule.applies_to("OFAC-SDN") is True
        assert rule.applies_to("Anything") is True


class TestVocabularyTree:
    def test_nests_subcategories_under_parent(self):
        tree = make_config().vocabulary_tree()
        assert tree["Sanctions"]["subcategories"]["Sanctioned"]["description"] == "sd"


class TestValidate:
    def test_clean_config_has_no_problems(self):
        assert _validate(make_config()) == []

    def test_subcategory_with_unknown_parent_is_flagged(self):
        cfg = make_config()
        cfg.subcategories["Orphan"] = {"parent": "NoSuchCategory", "description": None}

        problems = _validate(cfg)
        assert any("Orphan" in p for p in problems)

    def test_rule_with_unknown_match_type_is_flagged(self):
        cfg = make_config()
        cfg.rules[0].match_type = "fuzzy"  # not exact/contains/regex

        problems = _validate(cfg)
        assert any("MatchType" in p for p in problems)
