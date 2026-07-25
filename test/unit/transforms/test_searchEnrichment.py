"""
Unit tests for transforms/searchEnrichment.py

These functions precompute the "search-support" fields (a romanised name, a
token list, a phonetic key, a normalised id, an ISO2 country code) that
Elasticsearch would normally build. They are pure string transforms, so we can
pin their exact behaviour here.

country_to_iso2 accepts an explicit `valid_codes` set, so we test it without
needing the pickLists.xlsx file at all.
"""

import pytest

from transforms.searchEnrichment import (
    country_to_iso2,
    detect_language,
    normalize_number,
    normalize_text,
    phonetic_key,
    to_english,
    tokenize,
)

pytestmark = pytest.mark.unit


class TestToEnglish:
    def test_none_is_empty(self):
        assert to_english(None) == ""

    def test_latin_diacritics_are_folded(self):
        assert to_english("Müller") == "Muller"

    def test_non_latin_script_becomes_ascii(self):
        # Exact romanisation depends on anyascii, but it must be non-empty ASCII
        result = to_english("محمد")
        assert result != ""
        assert result.isascii()


class TestNormalizeText:
    def test_folds_lowercases_and_strips_punctuation(self):
        assert normalize_text("Frank Kakolele-Bwambale!") == "frank kakolele bwambale"

    def test_keeps_digits(self):
        assert normalize_text("Vessel 123") == "vessel 123"

    def test_none_is_empty(self):
        assert normalize_text(None) == ""


class TestTokenize:
    def test_splits_and_deduplicates_preserving_order(self):
        assert tokenize("Frank Kakolele Frank") == ["frank", "kakolele"]

    def test_empty_is_empty_list(self):
        assert tokenize("") == []


class TestPhoneticKey:
    def test_empty_is_empty(self):
        assert phonetic_key("") == ""

    def test_is_deterministic(self):
        assert phonetic_key("Johnson") == phonetic_key("Johnson")

    def test_sounds_alike_spellings_collide(self):
        # The whole point: differently-spelled but same-sounding names match.
        assert phonetic_key("Smith") == phonetic_key("Smyth")


class TestNormalizeNumber:
    def test_strips_separators_and_uppercases(self):
        assert normalize_number("a-1234 / 56") == "A123456"

    def test_strips_quotes_and_dots(self):
        assert normalize_number('AB.12"34') == "AB1234"

    def test_none_is_empty(self):
        assert normalize_number(None) == ""


class TestDetectLanguage:
    def test_latin(self):
        assert detect_language("Frank") == "Latin"

    def test_arabic(self):
        assert detect_language("محمد") == "Arabic"

    def test_no_letters_is_empty(self):
        assert detect_language("123 !!") == ""


class TestCountryToIso2:
    # A small explicit allow-list stands in for the picklist.
    VALID = {"IR", "US", "CD", "KP"}

    def test_alias_table_resolves_qualified_name(self):
        assert country_to_iso2("Iran (Islamic Republic of)", self.VALID) == "IR"

    def test_alias_table_resolves_historical_name(self):
        assert country_to_iso2("Zaire", self.VALID) == "CD"

    def test_pycountry_resolves_plain_name(self):
        assert country_to_iso2("United States", self.VALID) == "US"

    def test_resolved_but_not_allowed_returns_empty(self):
        # 'United States' resolves to US, but US is not in this allow-list
        assert country_to_iso2("United States", {"IR"}) == ""

    def test_collapsed_multi_country_string_returns_empty(self):
        assert country_to_iso2("['Chad', 'Sudan']", self.VALID) == ""

    def test_unresolvable_returns_empty(self):
        assert country_to_iso2("Atlantis", self.VALID) == ""

    def test_none_and_blank_return_empty(self):
        assert country_to_iso2(None, self.VALID) == ""
        assert country_to_iso2("   ", self.VALID) == ""
