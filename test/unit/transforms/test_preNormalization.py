"""
Unit tests for transforms/preNormalization.py

Two layers are tested:

  1. The individual handlers (enum, before_parenthesis, remove_list_markers,
     date_format) -- pure string transforms.
  2. The PreNormalizationEngine end-to-end -- built from two small pandas
     DataFrames (standing in for the Excel rule sheets) so we can run a whole
     record through detect-entity-type + rule application without any file.
"""

import pandas as pd
import pytest

from transforms.preNormalization import (
    BeforeParenthesisHandler,
    DateFormatHandler,
    EnumHandler,
    FlattenDictHandler,
    LanguageNameHandler,
    PreNormalizationEngine,
    RegexExtractHandler,
    RemoveListMarkersHandler,
    get_nested_values,
    parse_path,
    set_nested_value,
)

pytestmark = pytest.mark.unit


class TestFlattenDictHandler:
    OFFENSES = {"14": "Failure to comply", "16": "Unsatisfactory progress"}

    def test_default_joins_key_and_value_into_one_string(self):
        handler = FlattenDictHandler()
        assert handler.normalize(self.OFFENSES, "") == (
            "14: Failure to comply; 16: Unsatisfactory progress"
        )

    def test_list_mode_returns_one_string_per_entry(self):
        handler = FlattenDictHandler()
        assert handler.normalize(self.OFFENSES, "mode=list") == [
            "14: Failure to comply",
            "16: Unsatisfactory progress",
        ]

    def test_custom_format_and_newline_separator(self):
        handler = FlattenDictHandler()
        assert handler.normalize(self.OFFENSES, "format={value}|sep=\\n") == (
            "Failure to comply\nUnsatisfactory progress"
        )

    def test_non_dict_and_none_pass_through_unchanged(self):
        handler = FlattenDictHandler()
        assert handler.normalize("already a string", "") == "already a string"
        assert handler.normalize(None, "") is None
        assert handler.normalize({}, "") == ""


class TestLanguageNameHandler:
    """INTERPOL sends spoken languages as ISO 639-2/B codes; the handler
    resolves each to its English name via pycountry, leaving anything it
    cannot resolve (or a null) untouched so no value is ever dropped."""

    def test_maps_iso_639_2_codes_to_english_names(self):
        handler = LanguageNameHandler()
        assert handler.normalize("FRE", "") == "French"
        assert handler.normalize("GER", "") == "German"
        assert handler.normalize("CHI", "") == "Chinese"

    def test_tolerates_whitespace_and_case(self):
        handler = LanguageNameHandler()
        assert handler.normalize(" fre ", "") == "French"

    def test_unresolvable_collective_code_passes_through_unchanged(self):
        # ISO 639-2 collective codes (e.g. CAU Caucasian, DRA Dravidian) have
        # no single language name -> kept as-is rather than dropped.
        handler = LanguageNameHandler()
        assert handler.normalize("CAU", "") == "CAU"
        assert handler.normalize("DRA", "") == "DRA"

    def test_none_and_empty_pass_through_unchanged(self):
        handler = LanguageNameHandler()
        assert handler.normalize(None, "") is None
        assert handler.normalize("", "") == ""


class TestHandlers:
    def test_enum_maps_known_values_and_passes_through_unknown(self):
        handler = EnumHandler()
        rule = "Entity=Organization|Individual=Individual"

        assert handler.normalize("Entity", rule) == "Organization"
        assert handler.normalize("Individual", rule) == "Individual"
        assert handler.normalize("Other", rule) == "Other"  # unmapped -> unchanged
        assert handler.normalize(None, rule) is None

    def test_remove_list_markers(self):
        handler = RemoveListMarkersHandler()
        assert handler.normalize("(a) Some Name", "") == "Some Name"

    def test_before_parenthesis(self):
        handler = BeforeParenthesisHandler()
        assert handler.normalize("Listing Date (EO 14024):", "") == "Listing Date"

    def test_date_format(self):
        handler = DateFormatHandler()
        assert handler.normalize("03/29/1965", "MM/DD/YYYY") == "1965-03-29"
        assert handler.normalize("29/03/1965", "DD/MM/YYYY") == "1965-03-29"
        # not a date -> returned unchanged
        assert handler.normalize("not a date", "MM/DD/YYYY") == "not a date"
        # unknown rule -> returned unchanged
        assert handler.normalize("03/29/1965", "WEIRD") == "03/29/1965"

    def test_regex_extract_strips_wrapping_quotes(self):
        # Reuse the existing regex_extract handler as a quote-stripper: capture
        # the inside with the quotes made OPTIONAL, so the pattern always
        # matches (a "between quotes" pattern would return "" for unquoted
        # aliases and wipe them).
        handler = RegexExtractHandler()
        rule = r'^"?(.*?)"?$'

        # FBI wraps nickname aliases in double quotes -> stripped
        assert handler.normalize('"La Firma"', rule) == "La Firma"
        # already-unquoted alias -> unchanged (not wiped to "")
        assert handler.normalize("Manuel Perez", rule) == "Manuel Perez"
        # a quote *inside* the name is preserved
        assert handler.normalize('Ali "Bob" Smith', rule) == 'Ali "Bob" Smith'


class TestPathUtilities:
    def test_parse_path_marks_array_segments(self):
        assert parse_path("a.b[].c") == [("a", False), ("b", True), ("c", False)]

    def test_get_nested_values_scalar(self):
        data = {"a": {"b": "x"}}
        matches = get_nested_values(data, "a.b")

        assert len(matches) == 1
        assert matches[0][2] == "x"

    def test_get_nested_values_array(self):
        data = {"items": [{"v": 1}, {"v": 2}]}
        values = [m[2] for m in get_nested_values(data, "items[].v")]

        assert values == [1, 2]

    def test_set_nested_value_on_dict_and_list(self):
        d = {"k": "old"}
        set_nested_value(d, "k", "new")
        assert d["k"] == "new"

        lst = ["old"]
        set_nested_value(lst, 0, "new")
        assert lst[0] == "new"


class TestPreNormalizationEngineEndToEnd:
    def _engine(self):
        source_config_df = pd.DataFrame(
            [{"source": "OFAC", "entity_field": "type"}]
        )
        prenorm_df = pd.DataFrame([
            {"source": "OFAC", "field": "type", "entity_type": "*",
             "normalization_type": "enum", "normalization_rule": "person=Individual"},
            {"source": "OFAC", "field": "name", "entity_type": "*",
             "normalization_type": "before_parenthesis", "normalization_rule": ""},
        ])
        return PreNormalizationEngine(prenorm_df, source_config_df)

    def test_detect_entity_type_uses_enum_rule(self):
        engine = self._engine()
        assert engine.detect_entity_type("OFAC", {"type": "person"}) == "Individual"

    def test_pre_normalize_record_rewrites_entity_and_fields(self):
        engine = self._engine()

        result = engine.pre_normalize_record(
            "OFAC", {"type": "person", "name": "John (alias):"}
        )

        # entity field normalised from "person" -> "Individual"
        assert result["type"] == "Individual"
        # before_parenthesis applied to name
        assert result["name"] == "John"

    def test_original_record_is_not_mutated(self):
        engine = self._engine()
        raw = {"type": "person", "name": "John (alias):"}
        engine.pre_normalize_record("OFAC", raw)

        assert raw == {"type": "person", "name": "John (alias):"}  # deepcopied


class TestAliasDequoteEndToEnd:
    """The FBI-WANTED alias-dequote setup, driven end-to-end via regex_extract.

    FBI has no sourceConfig row, so its entity_type stays None and the rule must
    carry entity_type "*" to apply. ``aliases`` is a list of bare strings, so
    the field path needs the ``[]`` to reach each element -- ``aliases`` (no
    brackets) would hand the whole list to the handler at once.
    """

    RULE = r'^"?(.*?)"?$'

    def _engine(self, field):
        # Empty source config -> no entity_field for FBI-WANTED (type stays None).
        source_config_df = pd.DataFrame(columns=["source", "entity_field"])
        prenorm_df = pd.DataFrame([{
            "source": "FBI-WANTED", "field": field, "entity_type": "*",
            "normalization_type": "regex_extract", "normalization_rule": self.RULE,
        }])
        return PreNormalizationEngine(prenorm_df, source_config_df)

    def test_strips_quotes_per_alias_element(self):
        engine = self._engine("aliases[]")

        result = engine.pre_normalize_record(
            "FBI-WANTED",
            {"aliases": ['"La Firma"', "Manuel Perez", '"El Chess"']},
        )

        # each element de-quoted independently; unquoted one left alone
        assert result["aliases"] == ["La Firma", "Manuel Perez", "El Chess"]

    def test_field_without_brackets_corrupts_the_list(self):
        # Guards the "needs aliases[]" reasoning. Pointed at the bare list path,
        # regex_extract receives the whole list and stringifies it (str(value)),
        # corrupting it into a string. That is exactly why the rule must use
        # aliases[] -- so each element is handled individually.
        engine = self._engine("aliases")

        result = engine.pre_normalize_record("FBI-WANTED", {"aliases": ['"La Firma"']})

        # no longer the original list -> proves the bracketless path is wrong
        assert result["aliases"] != ['"La Firma"']
        assert isinstance(result["aliases"], str)
