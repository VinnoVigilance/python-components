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
# A trimmed slice of a real download (EU designated vessels), so the .xlsx read
# path -- separate from the .csv path -- is actually exercised.
REAL_XLSX = str(FIXTURES / "parsing" / "eu_vessels.xlsx")


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


def test_parses_real_xlsx():
    # Exercises the .xlsx branch (openpyxl) against a real-shaped file.
    records = list(TabularParser().parse(REAL_XLSX, config={}))

    assert len(records) > 0
    assert all(isinstance(r, dict) for r in records)
    # Every cell comes back as a string (NaN -> "").
    assert all(isinstance(v, str) for r in records for v in r.values())
