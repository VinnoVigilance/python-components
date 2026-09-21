"""Unit tests for MediaIdentityService -- the deterministic record_key and
external_id derivation. Pure logic, no I/O.
"""

import pytest

from services.adverseMediaPipeline.mediaIdentityService import MediaIdentityService

pytestmark = pytest.mark.unit


class TestGenerateRecordKey:

    def test_joins_configured_fields_with_pipe(self):
        source_config = {"identity": {"record_key": {"fields": ["source", "url"]}}}
        record = {"source": "AMLC", "url": "http://x/y"}
        assert (
            MediaIdentityService.generate_record_key(source_config, record)
            == "AMLC|http://x/y"
        )

    def test_source_config_value_wins_over_record(self):
        # A field present on the source config is preferred over the record's.
        source_config = {
            "source_name": "CONFIG_SRC",
            "identity": {"record_key": {"fields": ["source_name", "url"]}},
        }
        record = {"source_name": "RECORD_SRC", "url": "u"}
        assert (
            MediaIdentityService.generate_record_key(source_config, record)
            == "CONFIG_SRC|u"
        )

    def test_values_are_stripped(self):
        source_config = {"identity": {"record_key": {"fields": ["a"]}}}
        assert MediaIdentityService.generate_record_key(source_config, {"a": "  x  "}) == "x"

    def test_no_fields_configured_raises(self):
        with pytest.raises(ValueError, match="No record_key fields"):
            MediaIdentityService.generate_record_key({"identity": {}}, {"a": "x"})

    def test_missing_field_value_raises(self):
        source_config = {"identity": {"record_key": {"fields": ["a", "b"]}}}
        with pytest.raises(ValueError, match="Missing value for record_key field: b"):
            MediaIdentityService.generate_record_key(source_config, {"a": "x", "b": "  "})


class TestExtractExternalId:

    def test_returns_stripped_field_value(self):
        source_config = {"identity": {"external_id": {"field": "guid"}}}
        assert MediaIdentityService.extract_external_id(source_config, {"guid": " 42 "}) == "42"

    def test_no_external_id_field_returns_none(self):
        assert MediaIdentityService.extract_external_id({"identity": {}}, {"guid": "42"}) is None

    def test_missing_value_returns_none(self):
        source_config = {"identity": {"external_id": {"field": "guid"}}}
        assert MediaIdentityService.extract_external_id(source_config, {}) is None

    def test_empty_value_returns_none(self):
        source_config = {"identity": {"external_id": {"field": "guid"}}}
        assert MediaIdentityService.extract_external_id(source_config, {"guid": "   "}) is None
