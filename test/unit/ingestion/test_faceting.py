"""
Unit tests for the API collector's adaptive fan-out planner.

``plan_fanout`` is pure: the caller injects ``get_total(params) -> int``, so
these tests never touch the network. They drive the planner with a fake
``get_total`` and lock in the split behaviour of each facet type (enum, range,
substring) plus the invariant that every leaf ends up at or under the cap.
"""

import pytest

from ingestion.apiCollector.faceting import (
    COUNTRY_CODES,
    _resolve_values,
    plan_fanout,
)

pytestmark = pytest.mark.unit


def total_fn(mapping, default=0):
    """Build a get_total that looks totals up by their exact param set."""

    def get_total(params):
        return mapping.get(tuple(sorted(params.items())), default)

    return get_total


# --- no fan-out ------------------------------------------------------------

class TestNoFanout:
    def test_root_under_cap_is_a_single_leaf(self):
        plan = plan_fanout(
            get_total=total_fn({(): 5}),
            base_params={},
            cap=10,
            facets=[],
        )

        assert plan.root_total == 5
        assert plan.leaves == [{}]
        assert plan.unresolved == []

    def test_empty_dataset_produces_no_leaves(self):
        plan = plan_fanout(
            get_total=total_fn({(): 0}),
            base_params={},
            cap=10,
            facets=[{"type": "enum", "param": "c", "values": ["A"]}],
        )

        assert plan.leaves == []
        assert plan.unresolved == []


# --- enum ------------------------------------------------------------------

class TestEnumFacet:
    def test_disjoint_splits_and_early_stops(self):
        # base 25 = A 10 + B 10 + C 5; once the running remainder hits 0 after C
        # the planner must stop and never query D.
        get_total = total_fn({
            (): 25,
            (("country", "A"),): 10,
            (("country", "B"),): 10,
            (("country", "C"),): 5,
            (("country", "D"),): 999,
        })

        plan = plan_fanout(
            get_total=get_total,
            base_params={},
            cap=10,
            facets=[{
                "type": "enum", "param": "country",
                "values": ["A", "B", "C", "D"], "disjoint": True,
            }],
        )

        assert plan.leaves == [
            {"country": "A"}, {"country": "B"}, {"country": "C"},
        ]
        assert {"country": "D"} not in plan.leaves
        assert plan.unresolved == []

    def test_non_disjoint_visits_every_value(self):
        # Overlapping values (8 + 8 < 25) must all be visited -- no early stop.
        get_total = total_fn({
            (): 25,
            (("region", "X"),): 8,
            (("region", "Y"),): 8,
        })

        plan = plan_fanout(
            get_total=get_total,
            base_params={},
            cap=10,
            facets=[{
                "type": "enum", "param": "region",
                "values": ["X", "Y"], "disjoint": False,
            }],
        )

        assert plan.leaves == [{"region": "X"}, {"region": "Y"}]

    def test_incomplete_facet_hands_remainder_to_next_facet(self):
        # complete=False: the value slice is taken AND the untouched params are
        # passed on to the next facet as a catch-all for records with no value.
        get_total = total_fn({
            (): 25,
            (("cat", "A"),): 5,
            (("sub", "S"),): 5,
        })

        plan = plan_fanout(
            get_total=get_total,
            base_params={},
            cap=10,
            facets=[
                {"type": "enum", "param": "cat", "values": ["A"],
                 "complete": False},
                {"type": "enum", "param": "sub", "values": ["S"]},
            ],
        )

        assert {"cat": "A"} in plan.leaves
        assert {"sub": "S"} in plan.leaves


# --- range -----------------------------------------------------------------

class TestRangeFacet:
    def test_bisects_until_every_leaf_is_under_cap(self):
        values = list(range(0, 100, 4))  # 25 evenly spaced values

        def get_total(params):
            if "max" in params:
                return sum(params["min"] <= v <= params["max"] for v in values)
            return len(values)

        plan = plan_fanout(
            get_total=get_total,
            base_params={},
            cap=10,
            facets=[{
                "type": "range", "min_param": "min", "max_param": "max",
                "low": 0, "high": 100,
            }],
        )

        assert plan.root_total == 25
        assert plan.leaves  # something was produced
        for leaf in plan.leaves:
            assert get_total(leaf) <= 10
        assert plan.unresolved == []


# --- substring -------------------------------------------------------------

class TestSubstringFacet:
    def test_deepens_prefix_until_under_cap(self):
        get_total = total_fn({
            (): 25,
            (("q", "A"),): 12,
            (("q", "B"),): 12,
            (("q", "AA"),): 6,
            (("q", "AB"),): 6,
            (("q", "BA"),): 6,
            (("q", "BB"),): 6,
        })

        plan = plan_fanout(
            get_total=get_total,
            base_params={},
            cap=10,
            facets=[{
                "type": "substring", "param": "q",
                "alphabet": "AB", "max_depth": 2,
            }],
        )

        assert plan.leaves == [
            {"q": "AA"}, {"q": "AB"}, {"q": "BA"}, {"q": "BB"},
        ]
        assert plan.unresolved == []

    def test_still_over_cap_at_max_depth_is_reported_unresolved(self):
        # max_depth 1: the one-letter slices stay over cap and cannot deepen, so
        # they are emitted as leaves AND flagged unresolved for the caller.
        get_total = total_fn({
            (): 25,
            (("q", "A"),): 12,
            (("q", "B"),): 13,
        })

        plan = plan_fanout(
            get_total=get_total,
            base_params={},
            cap=10,
            facets=[{
                "type": "substring", "param": "q",
                "alphabet": "AB", "max_depth": 1,
            }],
        )

        assert plan.leaves == [{"q": "A"}, {"q": "B"}]
        assert plan.unresolved == [
            {"params": {"q": "A"}, "total": 12},
            {"params": {"q": "B"}, "total": 13},
        ]


# --- value resolution ------------------------------------------------------

class TestResolveValues:
    def test_inline_values_win(self):
        assert _resolve_values({"values": ["A", "B"]}) == ["A", "B"]

    def test_values_ref_resolves_builtin_country_codes(self):
        assert _resolve_values({"values_ref": "country_codes"}) == COUNTRY_CODES
        assert "PH" in COUNTRY_CODES

    def test_unknown_values_ref_is_empty(self):
        assert _resolve_values({"values_ref": "nope"}) == []
