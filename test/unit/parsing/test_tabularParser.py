"""
Unit tests for parsing/tabularParser.py

Runs the real TabularParser over a tiny committed CSV fixture
(test/fixtures/sample_tabular.csv). Confirms rows become string dicts, empty
cells become "", and an unsupported extension is rejected.
"""

from pathlib import Path

import pytest

from parsing.tabularParser import TabularParser

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE_CSV = str(FIXTURES / "sample_tabular.csv")


def test_parses_each_row():
    records = list(TabularParser().parse(SAMPLE_CSV, config={}))

    assert len(records) == 2
    assert records[0] == {"Name": "Acme Corp", "Country": "US", "Program": "SDN"}


def test_empty_cell_becomes_empty_string():
    records = list(TabularParser().parse(SAMPLE_CSV, config={}))

    # Beta Ltd's Country cell is blank in the CSV
    assert records[1]["Country"] == ""


def test_unsupported_extension_raises():
    parser = TabularParser()
    with pytest.raises(ValueError, match="Unsupported file type"):
        list(parser.parse("some_file.txt", config={}))
