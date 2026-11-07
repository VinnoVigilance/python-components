"""Verify expansion completeness, source ownership, and optional URL inputs."""

from copy import deepcopy

import pytest

from transforms.preProcessingEngine import PreProcessingEngine


pytestmark = pytest.mark.unit


@pytest.fixture
def engine():
    return PreProcessingEngine()


def test_expansion_preserves_case_context_without_mutating_input(engine):
    source = {"case_id": "case-1", "body": "Person: Alice; Person: Bob;"}
    original = deepcopy(source)
    records = engine.expand_regex_records([source], {
        "input_field": "body", "pattern": r"Person: (?P<name>[^;]+);",
    })
    assert [r["name"] for r in records] == ["Alice", "Bob"]
    assert all(r["case_id"] == "case-1" and r["body"] == source["body"] for r in records)
    assert source == original


def test_expansion_rejects_an_unmatched_case(engine):
    with pytest.raises(ValueError, match="No records matched"):
        engine.expand_regex_records([{"body": "unexpected source format"}], {
            "input_field": "body", "pattern": r"Person: (?P<name>[^;]+);",
        })


def test_expansion_requires_named_fields(engine):
    with pytest.raises(ValueError, match="named capture groups"):
        engine.expand_regex_records([], {"input_field": "body", "pattern": r"Person: (.+)"})


def test_boundary_check_rejects_partial_narrative_only_extraction(engine):
    with pytest.raises(ValueError, match="boundary"):
        engine.expand_regex_records([{"body": "Person: Alice; Person: Bob!"}], {
            "input_field": "body", "pattern": r"Person: (?P<name>[^;!]+);",
            "boundary_pattern": "Person:",
        })


@pytest.mark.parametrize("references", [[{"name": "Alice"}, {"name": "Bob"}], {"name": "Bob"}])
def test_expansion_rejects_missing_structured_names(engine, references):
    with pytest.raises(ValueError, match="Source names were not extracted"):
        engine.expand_regex_records([{"body": "Person: Alice;", "people": references}], {
            "input_field": "body", "pattern": r"Person: (?P<name>[^;]+);",
            "reference_field": "people", "reference_key": "name", "match_key": "name",
        })


def test_expansion_rejects_duplicate_person_identity(engine):
    with pytest.raises(ValueError, match="Repeated source identity"):
        engine.expand_regex_records([{"body": "Person: Alice; Person: ALICE;"}], {
            "input_field": "body", "pattern": r"Person: (?P<name>[^;]+);",
            "reference_field": "people", "reference_key": "name", "match_key": "name",
        })


@pytest.mark.parametrize("people", [[{"name": "Alice", "image": "/alice.jpg"}], {"name": "Alice", "image": "/alice.jpg"}])
def test_reference_lookup_accepts_single_object_and_array(engine, people):
    result = engine.resolve_reference({"name": "Alice", "people": people}, {
        "key_field": "name", "list_field": "people", "item_key": "name",
        "outputs": {"image": "photo_path"},
    })
    assert result["photo_path"] == "/alice.jpg"


def test_reference_lookup_does_not_assign_another_persons_image(engine):
    result = engine.resolve_reference({"name": "Bob", "people": [{"name": "Alice", "image": "/alice.jpg"}]}, {
        "key_field": "name", "list_field": "people", "item_key": "name",
        "outputs": {"image": "photo_path"},
    })
    assert result["photo_path"] == ""


@pytest.mark.parametrize("record", [{}, {"photo_path": ""}, {"photo_path": None}])
def test_optional_photo_keeps_person_when_input_is_missing(engine, record):
    result = engine.build_url_from_template({"name": "Alice", **record}, {
        "output_field": "photo_url", "template": "https://example.test{photo_path}",
        "when_field": "photo_path",
    })
    assert result["name"] == "Alice"
    assert result["photo_url"] == ""


def test_optional_guard_preserves_existing_url(engine):
    result = engine.build_url_from_template({"photo_url": "https://example.test/saved.jpg"}, {
        "output_field": "photo_url", "template": "https://example.test{photo_path}",
        "when_field": "photo_path",
    })
    assert result["photo_url"] == "https://example.test/saved.jpg"


def test_url_builder_still_requires_inputs_without_guard(engine):
    with pytest.raises(ValueError, match="Missing template"):
        engine.build_url_from_template({}, {
            "output_field": "photo_url", "template": "https://example.test{photo_path}",
        })


def test_optional_url_is_built_when_input_exists(engine):
    result = engine.build_url_from_template({"photo_path": "/alice.jpg"}, {
        "output_field": "photo_url", "template": "https://example.test{photo_path}",
        "when_field": "photo_path",
    })
    assert result["photo_url"] == "https://example.test/alice.jpg"
