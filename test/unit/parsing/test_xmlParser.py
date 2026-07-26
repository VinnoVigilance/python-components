"""
Unit tests for parsing/xmlParser.py

Runs the real XmlParser over a tiny committed fixture file
(test/fixtures/sample_designations.xml) and checks the records it yields. No
network, no downloads -- the sample ships with the repo.
"""

from pathlib import Path

import pytest

from parsing.xmlParser import XmlParser

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_XML = str(FIXTURES / "sample_designations.xml")
# A trimmed slice of a real download (UKSL), so the parser is exercised against
# real-world XML shape, not just the hand-written sample above.
REAL_XML = str(FIXTURES / "parsing" / "uksl.xml")


def test_parses_each_designation():
    records = list(XmlParser().parse(SAMPLE_XML, config={"root_tags": ["Designation"]}))

    assert len(records) == 2
    assert records[0]["Name"] == "John Smith"
    assert records[0]["Country"] == "US"
    assert records[1]["Name"] == "Jane Doe"


def test_attributes_are_captured():
    records = list(XmlParser().parse(SAMPLE_XML, config={"root_tags": ["Designation"]}))

    assert records[0]["id"] == "1"


def test_repeated_child_tag_becomes_a_list():
    records = list(XmlParser().parse(SAMPLE_XML, config={"root_tags": ["Designation"]}))

    # The first designation has two <Alias> children
    assert records[0]["Alias"] == ["Johnny", "J. Smith"]


def test_default_root_tag_when_no_config():
    # resolve_root_tags falls back to ["Designation"]
    records = list(XmlParser().parse(SAMPLE_XML))

    assert len(records) == 2


def test_non_matching_root_tag_yields_nothing():
    records = list(XmlParser().parse(SAMPLE_XML, root_tags=["DoesNotExist"]))

    assert records == []


class TestRealXml:
    """Parse a trimmed slice of a real UKSL download (root tag 'Designation')."""

    def test_yields_designation_records(self):
        records = list(XmlParser().parse(REAL_XML, root_tags=["Designation"]))

        assert len(records) > 0
        assert all(isinstance(r, dict) for r in records)
        # UniqueID is the list's external id -- present on every real record.
        assert all(r.get("UniqueID") for r in records)
