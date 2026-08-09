"""
Unit tests for transforms/postNormalization.py

Post-normalization runs a set of rule-driven handlers over each canonical
record: filling dependent fields, normalising dates, deduplicating arrays, and
computing the search-support fields. Each handler is a pure transform on an
entity dict, so we test them directly, plus the engine end-to-end from a small
rules DataFrame.
"""

import pandas as pd
import pytest

from transforms.postNormalization import (
    PostNormalizationEngine,
    _split_array_path,
    _strip_html,
    date_normalization_handler,
    deduplicate_all_arrays_handler,
    empty_dependency_handler,
    enum_normalize_handler,
    sanitize_html_handler,
    search_enrich_handler,
)

pytestmark = pytest.mark.unit


def test_split_array_path():
    assert _split_array_path("Names[].Name") == ("Names", "Name")


class TestSearchEnrichHandler:
    def test_normalize_writes_computed_field(self):
        entity = {"Names": [{"Name": "Frank Müller"}]}
        rule = {
            "condition_path": "Names[].Name",
            "target_path": "Names[].Normalized_Name",
            "action": "NORMALIZE",
        }
        search_enrich_handler(entity, rule)

        assert entity["Names"][0]["Normalized_Name"] == "frank muller"

    def test_script_is_derived_from_name_when_empty(self):
        entity = {"Names": [{"Name": "محمد"}]}
        rule = {
            "condition_path": "Names[].Name",
            "target_path": "Names[].Script",
            "action": "SCRIPT",
            "condition": "IF_EMPTY",
        }
        search_enrich_handler(entity, rule)

        # No source script -> derived from the name's characters
        assert entity["Names"][0]["Script"] == "Arabic"

    def test_if_empty_leaves_existing_script_untouched(self):
        entity = {"Names": [{"Name": "محمد", "Script": "Latin"}]}
        rule = {
            "condition_path": "Names[].Name",
            "target_path": "Names[].Script",
            "action": "SCRIPT",
            "condition": "IF_EMPTY",
        }
        search_enrich_handler(entity, rule)

        # Script already present -> the derive step does not override it
        assert entity["Names"][0]["Script"] == "Latin"

    def test_canonical_script_normalizes_source_value(self):
        entity = {"Names": [{"Name": "Frank", "Script": "Han"}]}
        rule = {
            "condition_path": "Names[].Script",
            "target_path": "Names[].Script",
            "action": "CANONICAL_SCRIPT",
        }
        search_enrich_handler(entity, rule)

        # A non-standard source label is mapped to the canonical one
        assert entity["Names"][0]["Script"] == "Chinese"


class TestDateNormalizationHandler:
    def test_resolves_date_rows(self):
        entity = {"Dates": [{"Year": "1980", "Month": "01", "Day": "15"}]}
        date_normalization_handler(entity, {"condition_path": "Dates"}, {"date_order": "DMY"})

        assert entity["Dates"][0]["FullDate"] == "1980-01-15"

    def test_missing_date_order_raises(self):
        entity = {"Dates": [{"Year": "1980"}]}
        with pytest.raises(ValueError, match="date_order"):
            date_normalization_handler(entity, {"condition_path": "Dates"}, {})


class TestEnumNormalizeHandler:
    # The rule that turns each source's raw approximate word into "true"/"false".
    RULE = {
        "condition_path": "Dates[].IsApproximate",
        "target_path": "Dates[].IsApproximate",
        "value": "true=true|approximately=true|circa=true|exact=false|false=false",
    }

    def test_maps_word_to_settled_value(self):
        entity = {"Dates": [{"IsApproximate": "APPROXIMATELY"}]}
        enum_normalize_handler(entity, self.RULE)

        # UN's "APPROXIMATELY" -> canonical "true" (case-insensitive)
        assert entity["Dates"][0]["IsApproximate"] == "true"

    def test_exact_becomes_false(self):
        entity = {"Dates": [{"IsApproximate": "EXACT"}]}
        enum_normalize_handler(entity, self.RULE)

        assert entity["Dates"][0]["IsApproximate"] == "false"

    def test_unlisted_word_is_left_unchanged(self):
        entity = {"Dates": [{"IsApproximate": "maybe"}]}
        enum_normalize_handler(entity, self.RULE)

        assert entity["Dates"][0]["IsApproximate"] == "maybe"


class TestDeduplicateAllArraysHandler:
    def test_removes_duplicate_array_items(self):
        entity = {"Names": [{"Name": "A"}, {"Name": "A"}, {"Name": "B"}]}
        deduplicate_all_arrays_handler(entity, {})

        assert entity["Names"] == [{"Name": "A"}, {"Name": "B"}]


class TestEmptyDependencyHandler:
    def test_fills_target_only_where_source_is_empty(self):
        entity = {"Names": [{"Name": ""}, {"Name": "Bob"}]}
        rule = {
            "condition_path": "Names[].Name",
            "target_path": "Names[].Type",
            "value": "primary",
        }
        empty_dependency_handler(entity, rule)

        assert entity["Names"][0]["Type"] == "primary"
        assert "Type" not in entity["Names"][1]


class TestSanitizeHtmlHandler:
    def test_removes_tags_and_unescapes_entities(self):
        assert (
            _strip_html("<p>Wanted for <b>fraud</b> &amp; theft.</p>")
            == "Wanted for fraud & theft."
        )

    def test_block_tags_become_space_not_glue(self):
        # </p><p> between sentences must not weld the words together
        assert _strip_html("the FBI</p><p>Reward up to") == "the FBI Reward up to"

    def test_double_encoded_tag_is_removed(self):
        # A literal &lt;p&gt; sitting inside real tags: unescape-first turns it
        # back into a <p> so the strip catches it too (the bug this guards).
        assert _strip_html("<p>&lt;p&gt;To provide info</p>") == "To provide info"

    def test_non_string_passes_through(self):
        assert _strip_html(None) is None

    def test_handler_cleans_every_element(self):
        entity = {
            "Comments": [
                {"type": "Summary", "text": "<p>armed &amp; dangerous</p>"},
                {"type": "Remarks", "text": "no markup here"},
            ]
        }
        sanitize_html_handler(entity, {"condition_path": "Comments[].text"})

        assert entity["Comments"][0]["text"] == "armed & dangerous"
        assert entity["Comments"][1]["text"] == "no markup here"

    def test_missing_array_is_a_no_op(self):
        entity = {"Names": [{"Name": "x"}]}
        sanitize_html_handler(entity, {"condition_path": "Comments[].text"})

        assert entity == {"Names": [{"Name": "x"}]}

    def test_non_string_leaf_is_left_untouched(self):
        # the isinstance(str) guard keeps structured values safe
        entity = {"additionalInfo": [{"Value": 5000}]}
        sanitize_html_handler(entity, {"condition_path": "additionalInfo[].Value"})

        assert entity["additionalInfo"][0]["Value"] == 5000


class TestEngineEndToEnd:
    def test_runs_rules_and_does_not_mutate_input(self):
        rules_df = pd.DataFrame([{
            "priority": 1,
            "rule_type": "SEARCH_ENRICH",
            "condition_path": "Names[].Name",
            "target_path": "Names[].Normalized_Name",
            "action": "NORMALIZE",
            "condition": "",
        }])
        engine = PostNormalizationEngine(rules_df, config={})

        record = {"Names": [{"Name": "Frank Müller"}]}
        result = engine.post_normalize_record(record)

        assert result["Names"][0]["Normalized_Name"] == "frank muller"
        # original untouched (engine deepcopies)
        assert "Normalized_Name" not in record["Names"][0]
