"""
Unit tests for parsing/pdfParser.py

The DNFBP fixture is a small real PDF (a few pages), so we test at two levels:

  * the parser's row/header LOGIC with synthetic input (instant, and it is where
    the real logic lives), and
  * one end-to-end test that parses the whole committed PDF, so the full extract
    path runs on every push and in CI.
"""

from functools import lru_cache
from pathlib import Path

import pytest

from parsing.pdfParser import PdfParser

pytestmark = pytest.mark.unit

FIXTURE = str(Path(__file__).resolve().parents[2] / "fixtures" / "parsing" / "dnfbp.pdf")


class TestRowAndHeaderLogic:
    """Fast tests of the pure parsing helpers -- no PDF needed."""

    def test_build_headers_cleans_cells_and_trims_trailing_empty(self):
        # A trailing empty header cell is trimmed off entirely (PDG-17),
        # while "NAME." is cleaned to "NAME".
        parser = PdfParser()
        headers = parser._build_headers(["INSTITUTION CODE", "NAME.", "   "])
        assert headers == ["INSTITUTION CODE", "NAME"]

    def test_build_headers_keeps_middle_empty_as_none(self):
        # Only *trailing* empties are trimmed; an empty header between real
        # ones becomes None so column positions are preserved.
        parser = PdfParser()
        assert parser._build_headers(["A", "", "B"]) == ["A", None, "B"]

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


class TestExpectedHeaderDetection:
    """PDG-17: locate the real header row when title rows precede it."""

    TABLE = [
        ["LIST OF ELECTED LOCAL OFFICIALS"],
        ["2025-2028"],
        ["REGION", "PROVINCE", "P/C/M", "POSITION", "NAME"],
        ["V", "Albay", "Municipality", "Mayor", "Juan Dela Cruz"],
    ]
    EXPECTED = ["REGION", "PROVINCE", "P/C/M", "POSITION", "NAME"]

    def test_finds_header_row_after_title_rows(self):
        parser = PdfParser()
        assert parser._find_header_row(self.TABLE, self.EXPECTED) == 2

    def test_subset_of_expected_headers_still_matches(self):
        parser = PdfParser()
        assert parser._find_header_row(self.TABLE, ["REGION", "NAME"]) == 2

    def test_returns_none_when_no_expected_headers(self):
        parser = PdfParser()
        assert parser._find_header_row(self.TABLE, []) is None

    def test_returns_none_when_header_absent(self):
        parser = PdfParser()
        assert parser._find_header_row([["foo"], ["bar"]], self.EXPECTED) is None

    def test_repeated_header_with_expected_delegates_to_find(self):
        parser = PdfParser()
        table = [["title"], ["REGION", "PROVINCE", "NAME"]]
        assert (
            parser._find_repeated_header(
                table=table,
                headers=["REGION", "PROVINCE", "NAME"],
                expected_headers=["REGION", "NAME"],
            )
            == 1
        )

    def test_repeated_header_without_expected_checks_first_row(self):
        parser = PdfParser()
        headers = ["REGION", "PROVINCE", "NAME"]
        assert parser._find_repeated_header([headers, ["x"]], headers) == 0
        assert parser._find_repeated_header([["data", "row"]], headers) is None


class TestTableCleaning:
    """PDG-17: generic structural cleanup helpers."""

    def test_trim_trailing_empty_cells(self):
        parser = PdfParser()
        assert parser._trim_trailing_empty_cells(["a", "b", "", None, "  "]) == ["a", "b"]

    def test_trim_keeps_middle_empty_cells(self):
        parser = PdfParser()
        assert parser._trim_trailing_empty_cells(["a", "", "b"]) == ["a", "", "b"]

    def test_trim_none_row(self):
        parser = PdfParser()
        assert parser._trim_trailing_empty_cells(None) == []

    def test_clean_table_drops_empty_rows_and_trims(self):
        parser = PdfParser()
        table = [["A", "B", ""], None, ["", "  "], ["C", "D"]]
        assert parser._clean_table(table) == [["A", "B"], ["C", "D"]]

    def test_row_has_value(self):
        parser = PdfParser()
        assert parser._row_has_value(["", "  ", None]) is False
        assert parser._row_has_value(["", "x"]) is True

    def test_normalize_header_for_matching(self):
        parser = PdfParser()
        assert parser._normalize_header_for_matching("name.") == "NAME"
        assert parser._normalize_header_for_matching("  Foo   Bar ") == "FOO BAR"
        assert parser._normalize_header_for_matching("   ") is None
        assert parser._normalize_header_for_matching(None) is None

    def test_clean_value(self):
        parser = PdfParser()
        assert parser._clean_value("a\nb ") == "a b"
        assert parser._clean_value(None) is None
        assert parser._clean_value(123) == 123


@lru_cache(maxsize=1)
def _real_records():
    return tuple(PdfParser().parse(FIXTURE))


class TestRealPdf:
    """End-to-end over the whole real committed PDF."""

    def test_extracts_rows(self):
        records = _real_records()
        assert len(records) > 0
        assert all(isinstance(r, dict) for r in records)

    def test_rows_carry_the_institution_code_column(self):
        assert any("INSTITUTION CODE" in r for r in _real_records())
