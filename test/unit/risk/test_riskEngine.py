"""
Unit tests for risk/riskEngine.py - the single-member DB entry point.

The engine adapts the existing rule/LLM classifier to one
``core.watchlist_member.full_payload`` and shapes the result into the
``risk_details`` object stored in ``core.member_risk_category``.
"""

from unittest.mock import MagicMock

import pytest

from risk import riskEngine

pytestmark = pytest.mark.unit


def test_primary_list_name_reads_first_source():
    payload = {"Sources": [{"ListName": "OFAC-SDN"}, {"ListName": "UN-SANCTIONS"}]}
    assert riskEngine._primary_list_name(payload) == "OFAC-SDN"


def test_primary_list_name_none_when_absent():
    assert riskEngine._primary_list_name({}) is None
    assert riskEngine._primary_list_name({"Sources": [{}]}) is None


def test_classify_wraps_rule_labels_into_risk_details():
    # Build an engine without touching the Excel config, then swap the rule
    # matcher for a stub so we isolate the shaping logic.
    engine = riskEngine.RiskEngine(cfg=MagicMock(), use_llm=False)
    labels = [{"Category": "Sanctions", "SubCategory": "Sanctioned"}]
    engine.rules = MagicMock()
    engine.rules.classify_record.return_value = {"RiskCategories": labels}

    details = engine.classify({"Sources": [{"ListName": "OFAC-SDN"}]})

    assert details == {"RiskCategories": labels}
    # The authoritative list name is forwarded to the matcher.
    assert engine.rules.classify_record.call_args.kwargs["list_name"] == "OFAC-SDN"


def test_classify_empty_when_no_labels():
    engine = riskEngine.RiskEngine(cfg=MagicMock(), use_llm=False)
    engine.rules = MagicMock()
    engine.rules.classify_record.return_value = {"RiskCategories": []}

    details = engine.classify({})

    assert details == {"RiskCategories": []}
