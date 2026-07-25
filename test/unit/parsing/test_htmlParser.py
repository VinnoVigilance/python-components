"""
Unit tests for parsing/htmlParser.py

Runs the real HtmlParser over the committed ATC fixtures. The parser dispatches
by config["list_name"] to the per-list handler, so we exercise both ATC lists.
"""

from pathlib import Path

import pytest

from parsing.htmlParser import HtmlParser

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "parsing"
INDIVIDUALS = str(FIXTURES / "atc_individuals.html")
GROUPS = str(FIXTURES / "atc_groups.html")


def test_parses_atc_individuals():
    records = HtmlParser().parse(
        INDIVIDUALS, {"list_name": "ATC-DESIGNATED-TERRORIST-INDIVIDUALS"}
    )

    assert len(records) > 0
    first = records[0]
    assert first["entity_type"] == "Individual"
    assert first["name"]  # non-empty
    for key in ("category", "atc_resolution_no", "date_issued", "detail_url"):
        assert key in first


def test_parses_atc_groups():
    records = HtmlParser().parse(
        GROUPS, {"list_name": "ATC-DESIGNATED-TERRORIST-GROUPS"}
    )

    assert len(records) > 0
    assert records[0]["entity_type"] == "Entity"
    assert records[0]["name"]


def test_unknown_list_name_raises():
    with pytest.raises(ValueError, match="No HTML handler"):
        HtmlParser().parse(INDIVIDUALS, {"list_name": "SOMETHING-ELSE"})
