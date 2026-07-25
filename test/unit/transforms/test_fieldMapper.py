"""
Unit tests for transforms/fieldMapper.py

The field mapper turns a raw source record into the canonical schema, driven by
a list of Rule objects. Because a MappingEngine is built from Rule objects
directly, we can test the whole mapping end-to-end WITHOUT the mapping.xlsx
file -- we hand-build a few rules and check the output.

We also test the small pure helpers (JsonPath, unquote, entity-type detection,
placeholder handling) that the engine relies on.
"""

import pytest

from transforms.fieldMapper import (
    JsonPath,
    MappingEngine,
    Rule,
    default_value,
    detect_entity_type,
    drop_placeholder,
    normalize_bool,
    strip_anchor_prefix,
    unquote,
    values_equal,
)

pytestmark = pytest.mark.unit


class TestJsonPath:
    def test_split_ignores_blank_segments(self):
        assert JsonPath.split("a.b.c") == ["a", "b", "c"]
        assert JsonPath.split("a..b") == ["a", "b"]
        assert JsonPath.split("") == []

    def test_get_nested_value(self):
        data = {"a": {"b": "x"}}
        assert JsonPath.get(data, "a.b") == "x"

    def test_get_missing_is_none(self):
        assert JsonPath.get({"a": 1}, "a.b.c") is None

    def test_get_all_expands_list_segments(self):
        data = {"names": [{"full": "A"}, {"full": "B"}]}
        assert JsonPath.get_all(data, "names[].full") == ["A", "B"]

    def test_set_creates_nested_dicts(self):
        data = {}
        JsonPath.set(data, "a.b", "x")
        assert data == {"a": {"b": "x"}}

    def test_set_strips_list_markers(self):
        data = {}
        JsonPath.set(data, "a[].b", "x")
        assert data == {"a": {"b": "x"}}


class TestSmallHelpers:
    def test_unquote(self):
        assert unquote('"hi"') == "hi"
        assert unquote("hi") == "hi"
        assert unquote(None) == ""

    def test_normalize_bool(self):
        assert normalize_bool(True) == "true"
        assert normalize_bool(False) == "false"
        assert normalize_bool("  YES ") == "yes"

    def test_values_equal_across_types(self):
        assert values_equal(True, "true") is True
        assert values_equal("A", "a") is True
        assert values_equal("x", "y") is False

    def test_default_value_by_type(self):
        assert default_value("object") == {}
        assert default_value("array") == []
        assert default_value("json") is None
        assert default_value("string") == ""

    def test_drop_placeholder_turns_nothing_words_into_none(self):
        assert drop_placeholder("N/A") is None
        assert drop_placeholder("UNKNOWN") is None
        assert drop_placeholder("Baghdad") == "Baghdad"
        assert drop_placeholder(123) == 123

    def test_strip_anchor_prefix(self):
        assert strip_anchor_prefix("names[].full", "names[]") == "full"
        assert strip_anchor_prefix("names[]", "names[]") == ""
        assert strip_anchor_prefix("other", "names[]") == "other"


class TestDetectEntityType:
    def test_individual(self):
        assert detect_entity_type({"entity_type": "individual"}) == "Individual"

    def test_entity_synonyms(self):
        assert detect_entity_type({"entity_type": "organization"}) == "Entity"

    def test_vessel_by_type_field(self):
        assert detect_entity_type({"Type": "vessel"}) == "Vessel"

    def test_vessel_inferred_from_imo(self):
        assert detect_entity_type({"IMO number": "1234567"}) == "Vessel"

    def test_defaults_to_individual(self):
        assert detect_entity_type({}) == "Individual"


class TestMappingEngineEndToEnd:
    def test_scalar_path_and_constant(self):
        rules = [
            Rule("Individual", "fullName", "string", None, "path", "name"),
            Rule("Individual", "source", "string", None, "constant", "OFAC"),
        ]
        engine = MappingEngine(rules)

        result = engine.map_record({"entity_type": "individual", "name": "Frank"})

        assert result == {"fullName": "Frank", "source": "OFAC"}

    def test_group_builds_one_object(self):
        rules = [
            Rule("Individual", "addresses[].city", "string", "addr", "path", "city"),
            Rule("Individual", "addresses[].country", "string", "addr", "constant", "US"),
        ]
        engine = MappingEngine(rules)

        result = engine.map_record({"entity_type": "individual", "city": "Baghdad"})

        assert result["addresses"] == [{"city": "Baghdad", "country": "US"}]

    def test_path_expand_produces_one_object_per_item(self):
        rules = [
            # the anchor rule (no leaf) marks which list to expand over
            Rule("Individual", "aliases[]", "string", "al", "path_expand", "akaList[]"),
            # each expanded item fills the leaf from its own "name"
            Rule("Individual", "aliases[].name", "string", "al", "path", "name"),
        ]
        engine = MappingEngine(rules)

        raw = {
            "entity_type": "individual",
            "akaList": [{"name": "Bob"}, {"name": "Bobby"}],
        }
        result = engine.map_record(raw)

        assert result["aliases"] == [{"name": "Bob"}, {"name": "Bobby"}]

    def test_rules_for_other_entity_types_are_ignored(self):
        rules = [
            Rule("Individual", "fullName", "string", None, "path", "name"),
            Rule("Vessel", "imo", "string", None, "path", "IMO number"),
        ]
        engine = MappingEngine(rules)

        # An individual record must not pick up the Vessel-only rule
        result = engine.map_record({"entity_type": "individual", "name": "Frank"})

        assert "imo" not in result
        assert result["fullName"] == "Frank"
