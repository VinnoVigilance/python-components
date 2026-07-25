"""
Unit tests for transforms/preProcessingEngine.py

The preprocessing engine runs source-specific cleanups over parsed records
before mapping (merging split rows, detecting entity type from a name, deriving
ids, splitting combined fields, dropping incomplete rows). Most handlers are
pure record-in / record-out transforms, so we test them directly.

Handlers that read files from disk (enrich_atc_profile_data) are left for a
fixture-based test later.
"""

import pytest

from transforms.preProcessingEngine import PreProcessingEngine

pytestmark = pytest.mark.unit


@pytest.fixture()
def engine():
    return PreProcessingEngine()


class TestDetectEntityType:
    def test_org_keyword_is_entity(self, engine):
        record = engine.detect_entity_type(
            {"name": "ACME TRADING LLC"}, {"input_field": "name"}
        )
        assert record["detected_entity_type"] == "Entity"

    def test_person_name_is_individual(self, engine):
        record = engine.detect_entity_type(
            {"name": "John Smith"}, {"input_field": "name"}
        )
        assert record["detected_entity_type"] == "Individual"

    def test_custom_output_field(self, engine):
        record = engine.detect_entity_type(
            {"n": "John Smith"},
            {"input_field": "n", "output_field": "kind"},
        )
        assert record["kind"] == "Individual"


class TestGenerateAtcUniqueId:
    def test_id_shape_and_determinism(self, engine):
        record = {"name": "Test Person", "atc_resolution_no": "Resolution No. 123"}
        first = engine.generate_atc_unique_id(dict(record), {})["unique_id"]
        second = engine.generate_atc_unique_id(dict(record), {})["unique_id"]

        assert first.startswith("ATC-123-")
        assert first == second  # deterministic for the same input

    def test_missing_resolution_becomes_unknown(self, engine):
        record = engine.generate_atc_unique_id(
            {"name": "X", "atc_resolution_no": "no number here"}, {}
        )
        assert record["unique_id"].startswith("ATC-UNKNOWN-")


class TestExtractNameFromUrl:
    def test_extracts_slug(self, engine):
        record = engine.extract_name_from_url(
            {"detail_url": "https://example.com/profile/john-doe/"}, {}
        )
        assert record["extracted_name_from_url"] == "john-doe"

    def test_empty_url_gives_empty(self, engine):
        record = engine.extract_name_from_url({"detail_url": ""}, {})
        assert record["extracted_name_from_url"] == ""


class TestMergeDfatSplitRecords:
    def test_rows_sharing_a_reference_are_merged(self, engine):
        records = [
            {"Reference": "12", "Name of Individual or Entity": "John",
             "Name Type": "Primary", "Alias Strength": "Strong", "Type": "Individual"},
            {"Reference": "12a", "Name of Individual or Entity": "Johnny",
             "Name Type": "AKA", "Alias Strength": "Weak"},
        ]
        result = engine.merge_dfat_split_records(records, {})

        assert len(result) == 1
        assert result[0]["Reference"] == "12"
        assert len(result[0]["Names"]) == 2
        assert result[0]["Type"] == "Individual"


class TestFixEuVesselMultilineRows:
    def test_drops_rows_without_vessel_and_clears_ref_errors(self, engine):
        records = [
            {"Vessel name at designation time": "Ship A", "IMO number": "123",
             "Date of application": "#REF!",
             "Link to relevant EU Official Journal ": "x"},
            {"Vessel name at designation time": "", "IMO number": "456"},
        ]
        result = engine.fix_eu_vessel_multiline_rows(records, {})

        assert len(result) == 1
        assert result[0]["Date of application"] == ""  # #REF! cleared


class TestFilterMissingRequiredField:
    def test_drops_records_missing_the_field(self, engine):
        records = [{"id": "1"}, {"id": ""}, {"other": "x"}]
        result = engine.filter_missing_required_field(records, {"field": "id"})

        assert result == [{"id": "1"}]


class TestSplitAtcDateAndPlaceOfBirth:
    def test_splits_date_and_place(self, engine):
        record = {
            "profile_data": {
                "profile_fields": {"Date and Place of Birth": "1980, Manila"}
            }
        }
        result = engine.split_atc_date_and_place_of_birth(record, {})

        assert result["atc_birth_date"] == "1980"
        assert result["atc_birth_place"] == "Manila"

    def test_place_only_when_no_digits(self, engine):
        record = {
            "profile_data": {
                "profile_fields": {"Date and Place of Birth": "Manila City"}
            }
        }
        result = engine.split_atc_date_and_place_of_birth(record, {})

        assert result["atc_birth_date"] == ""
        assert result["atc_birth_place"] == "Manila City"


class TestPreprocessOrchestrator:
    def test_runs_record_level_handler(self, engine):
        rules = [{
            "handler": "extract_name_from_url",
            "level": "record",
            "config": {"input_field": "detail_url", "output_field": "slug"},
        }]
        result = engine.preprocess([{"detail_url": "https://x.com/a/abc"}], rules)

        assert result[0]["slug"] == "abc"

    def test_unknown_handler_raises(self, engine):
        with pytest.raises(ValueError, match="handler"):
            engine.preprocess([{}], [{"handler": "does_not_exist", "level": "record"}])

    def test_no_rules_returns_records_unchanged(self, engine):
        records = [{"a": 1}]
        assert engine.preprocess(records, []) == records
