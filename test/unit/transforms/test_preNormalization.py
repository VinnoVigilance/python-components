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
    PreNormalizationEngine,
    RemoveListMarkersHandler,
    get_nested_values,
    parse_path,
    set_nested_value,
)

pytestmark = pytest.mark.unit


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
