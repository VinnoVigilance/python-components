"""
Unit tests for parsing/pdfParser.py

The DNFBP fixture is a real 210-page PDF, and extracting tables from all of it
takes minutes -- too slow for every run. So:

  * the parser's row/header LOGIC is tested with synthetic input (instant, and
    it is where the real logic lives), and
  * one end-to-end test parses the real committed PDF, marked `slow` so it is
    skipped unless you pass `--runslow` (see conftest.py). CI skips it too.

Run the slow one explicitly with:  pytest --runslow test/unit/parsing/test_pdfParser.py
"""

from functools import lru_cache
from pathlib import Path

import pytest

from parsing.pdfParser import PdfParser

pytestmark = pytest.mark.unit

FIXTURE = str(Path(__file__).resolve().parents[2] / "fixtures" / "parsing" / "dnfbp.pdf")


class TestRowAndHeaderLogic:
    """Fast tests of the pure parsing helpers -- no PDF needed."""

    def test_build_headers_cleans_cells(self):
        parser = PdfParser()
        headers = parser._build_headers(["INSTITUTION CODE", "NAME.", "   "])
        assert headers == ["INSTITUTION CODE", "NAME", None]

    def test_row_to_record_maps_cells_to_headers(self):
        parser = PdfParser()
        record = parser._row_to_record(["A001", "Acme"], ["INSTITUTION CODE", "NAME"])
        assert record == {"INSTITUTION CODE": "A001", "NAME": "Acme"}

    def test_row_to_record_drops_all_empty_row(self):
        parser = PdfParser()
        assert parser._row_to_record(["", None], ["INSTITUTION CODE", "NAME"]) is None

    def test_is_same_header_detects_repeated_header(self):
        parser = PdfParser()
        headers = ["INSTITUTION CODE", "NAME", "ADDRESS"]
        assert parser._is_same_header(["INSTITUTION CODE", "NAME", "ADDRESS"], headers) is True
        assert parser._is_same_header(["A001", "Acme", "Somewhere"], headers) is False

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            PdfParser().parse("does_not_exist.pdf")


@lru_cache(maxsize=1)
def _real_records():
    return tuple(PdfParser().parse(FIXTURE))


@pytest.mark.slow
class TestRealPdf:
    """End-to-end over the real committed PDF. Slow; opt-in via --runslow."""

    def test_extracts_rows(self):
        records = _real_records()
        assert len(records) > 0
        assert all(isinstance(r, dict) for r in records)

    def test_rows_carry_the_institution_code_column(self):
        assert any("INSTITUTION CODE" in r for r in _real_records())
