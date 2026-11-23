"""
Unit tests for parsing/pdfParser.py

The DNFBP fixture is a small real PDF (a few pages), so we test at two levels:

  * the parser's row/header LOGIC with synthetic input (instant, and it is where
    the real logic lives), and
  * one end-to-end test that parses the whole committed PDF, so the full extract
    path runs on every push and in CI.
"""

from collections import Counter
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from parsing.pdfParser import PdfParser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS

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


def test_image_cards_extracts_all_populated_pro9_cards():
    config = WATCHLIST_CONFIGS["PNP_PRO9_MOST_WANTED_2024"]
    records = list(PdfParser().parse(
        Path(__file__).resolve().parents[2] / "fixtures" / "parsing"
        / "pnp_pro9_most_wanted_2024.pdf",
        config,
    ))

    assert len(records) == 34
    assert Counter(record["source_page"] for record in records) == {
        1: 10, 2: 10, 3: 4, 4: 10,
    }
    assert records[0]["name_raw"] == "GEREMIAS EYAS A.K.A. PLUTO"
    assert "L-00948" in records[0]["details_raw"]
    assert "Iligan City" in records[9]["details_raw"]
    assert records[-1]["source_position"] == 10


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "parsing"
TEXT_MODE = {"parser": {"type": "pdf", "mode": "text"}}


class TestTextMode:
    """Text mode: the whole document as one record (SEC advisory PDFs)."""

    def test_returns_one_record_with_the_full_text(self):
        records = PdfParser().parse(FIXTURES / "sec_advisory_text.pdf", TEXT_MODE)

        assert len(records) == 1
        body = records[0]["BodyText"]
        assert body.startswith("A D V I S O R Y")
        assert "EXNESS GLOBAL LIMITED" in body
        assert "NOT\nAUTHORIZED TO SOLICIT INVESTMENTS FROM THE PUBLIC" in body
        assert "https://linktr.ee/secphilippines" in body

    def test_joins_pages_without_page_markers(self):
        body = PdfParser().parse(FIXTURES / "sec_advisory_text.pdf", TEXT_MODE)[0]["BodyText"]

        assert "--- PAGE" not in body
        assert "\n\n" in body

    def test_scanned_pdf_without_a_text_layer_gives_none(self):
        records = PdfParser().parse(FIXTURES / "sec_advisory_scanned.pdf", TEXT_MODE)

        assert records == [{"BodyText": None}]

    def test_mode_is_case_insensitive(self):
        records = PdfParser().parse(
            FIXTURES / "sec_advisory_text.pdf", {"parser": {"mode": " TEXT "}}
        )

        assert list(records[0]) == ["BodyText"]

    def test_without_text_mode_the_table_parser_still_runs(self):
        records = PdfParser().parse(FIXTURE, {"parser": {"type": "pdf"}})

        assert records == list(_real_records())

    def test_missing_file_raises_in_text_mode(self):
        with pytest.raises(FileNotFoundError):
            PdfParser().parse("does_not_exist.pdf", TEXT_MODE)


class TestTextOcrFallback:
    """Native text stays primary; OCR is an explicitly configured alternative."""

    def test_native_text_does_not_invoke_ocr(self, monkeypatch):
        parser = PdfParser()
        ocr = MagicMock(side_effect=AssertionError("Native text must not invoke OCR"))
        monkeypatch.setattr(parser, "_ocr_text_record", ocr)

        [record] = parser.parse(FIXTURES / "sec_advisory_text.pdf", {
            "parser": {"mode": "text", "ocr_fallback": {}, "require_text": True},
        })

        assert "EXNESS GLOBAL LIMITED" in record["BodyText"]
        assert "_ocr" not in record
        ocr.assert_not_called()

    def test_scanned_pdf_uses_fallback_and_keeps_recognition_evidence(self, monkeypatch):
        parser = PdfParser()
        recognized = {"BodyText": "Recognized decision", "_ocr": {"pages": [{"page_number": 1}]}}
        ocr = MagicMock(return_value=recognized)
        monkeypatch.setattr(parser, "_ocr_text_record", ocr)
        path = FIXTURES / "sec_advisory_scanned.pdf"

        assert parser.parse(path, {"parser": {"mode": "text", "ocr_fallback": {}}}) == [recognized]
        ocr.assert_called_once_with(path, {})

    @pytest.mark.parametrize("fallback", [False, True])
    def test_require_text_rejects_empty_output_and_keeps_original(self, monkeypatch, fallback):
        parser = PdfParser()
        monkeypatch.setattr(parser, "_page_texts", lambda _: [])
        monkeypatch.setattr(parser, "_ocr_text_record", lambda *_: {"BodyText": None, "_ocr": {"pages": []}})
        path = FIXTURES / "sec_advisory_scanned.pdf"
        before = path.read_bytes()
        settings = {"mode": "text", "require_text": True}
        if fallback:
            settings["ocr_fallback"] = {}

        with pytest.raises(ValueError, match="returned no text"):
            parser.parse(path, {"parser": settings})

        assert path.read_bytes() == before

    def test_empty_ocr_output_is_retained_when_text_is_not_required(self, monkeypatch):
        parser = PdfParser()
        monkeypatch.setattr(parser, "_page_texts", lambda _: [])
        recognized = {"BodyText": None, "_ocr": {"pages": []}}
        monkeypatch.setattr(parser, "_ocr_text_record", lambda *_: recognized)

        assert parser.parse(FIXTURE, {"parser": {"mode": "text", "ocr_fallback": {}}}) == [recognized]

    def test_page_ocr_only_replaces_empty_pages_in_original_order(self, monkeypatch):
        parser = PdfParser()
        native = MagicMock(return_value=["First native page", "", "Third native page", ""])
        monkeypatch.setattr(parser, "_page_texts", native)
        evidence = {"pages": [
            {"page_number": 2, "lines": [{"text": "Second"}, {"text": "recognized page"}]},
            {"page_number": 4, "lines": [{"text": "Fourth recognized page"}]},
        ]}
        ocr = MagicMock(return_value={"BodyText": "OCR-only text", "_ocr": evidence})
        monkeypatch.setattr(parser, "_ocr_text_record", ocr)
        path = Path(FIXTURE)

        [record] = parser.parse(path, {"parser": {"mode": "text", "ocr_empty_pages": True, "ocr_fallback": {}}})

        native.assert_called_once_with(path, keep_empty=True)
        ocr.assert_called_once_with(path, {}, [2, 4])
        assert record == {"BodyText": "First native page\n\nSecond\nrecognized page\n\nThird native page\n\nFourth recognized page", "_ocr": evidence}

    def test_page_ocr_does_not_recognize_pages_that_have_native_text(self, monkeypatch):
        parser = PdfParser()
        monkeypatch.setattr(parser, "_page_texts", lambda *_, **__: ["One", "Two"])
        ocr = MagicMock()
        monkeypatch.setattr(parser, "_ocr_text_record", ocr)

        assert parser.parse(FIXTURE, {"parser": {"mode": "text", "ocr_empty_pages": True, "ocr_fallback": {}}}) == [{"BodyText": "One\n\nTwo"}]
        ocr.assert_not_called()

    def test_page_ocr_requires_explicit_fallback_configuration(self):
        with pytest.raises(ValueError, match="requires ocr_fallback"):
            PdfParser().parse(FIXTURE, {"parser": {"mode": "text", "ocr_empty_pages": True}})

    def test_page_texts_preserves_empty_positions_only_when_requested(self, monkeypatch):
        import parsing.pdfParser as module

        pdf = MagicMock()
        pdf.__enter__.return_value.pages = [
            MagicMock(extract_text=MagicMock(return_value=" Native ")),
            MagicMock(extract_text=MagicMock(return_value=None)),
            MagicMock(extract_text=MagicMock(return_value="  ")),
        ]
        monkeypatch.setattr(module.pdfplumber, "open", lambda _: pdf)

        assert PdfParser()._page_texts(FIXTURE) == ["Native"]
        assert PdfParser()._page_texts(FIXTURE, keep_empty=True) == ["Native", "", ""]

    @pytest.mark.parametrize("settings", [None, False, {"unknown": True}])
    def test_fallback_rejects_invalid_configuration(self, settings):
        with pytest.raises(ValueError, match="only engine/document"):
            PdfParser._ocr_text_record(Path(FIXTURE), settings)

    @pytest.mark.parametrize("override", [False, True])
    def test_fallback_reuses_reader_and_engine_defaults_or_overrides(self, monkeypatch, override):
        from parsing.ocr import documentOcrReader, rapidOcrEngine

        evidence = {"pages": [
            {"page_number": 1, "lines": [{"text": "First line"}, {"text": "Second line"}]},
            {"page_number": 2, "lines": [{"text": "Next page"}]},
        ]}
        reader = MagicMock()
        reader.read.return_value = evidence
        reader_factory = MagicMock(return_value=reader)
        engine_factory = MagicMock()
        monkeypatch.setattr(documentOcrReader, "DocumentOcrReader", reader_factory)
        monkeypatch.setattr(rapidOcrEngine, "RapidOcrEngine", engine_factory)
        settings = ({"engine": {"use_angle_classification": False},
                     "document": {"pdf_render_dpi": 300, "max_pages": 20}} if override else {})
        path = Path(FIXTURE)

        result = PdfParser._ocr_text_record(path, settings)

        engine_settings = engine_factory.call_args.args[0]
        document_settings = reader_factory.call_args.args[1]
        assert engine_settings.recognition_language == "EN"
        assert engine_settings.use_angle_classification is (not override)
        assert document_settings.pdf_render_dpi == (300 if override else 200)
        assert document_settings.max_pages == (20 if override else 500)
        reader_factory.assert_called_once_with(engine_factory.return_value, document_settings)
        reader.read.assert_called_once_with(path, page_numbers=None)
        assert result == {"BodyText": "First line\nSecond line\n\nNext page", "_ocr": evidence}

    def test_ocr_failure_propagates_instead_of_returning_partial_text(self, monkeypatch):
        parser = PdfParser()
        monkeypatch.setattr(parser, "_page_texts", lambda *_, **__: ["Native page", ""])
        monkeypatch.setattr(parser, "_ocr_text_record", MagicMock(side_effect=RuntimeError("OCR page failed")))

        with pytest.raises(RuntimeError, match="OCR page failed"):
            parser.parse(FIXTURE, {"parser": {"mode": "text", "ocr_empty_pages": True, "ocr_fallback": {}}})
