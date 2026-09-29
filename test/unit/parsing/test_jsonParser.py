"""
Unit tests for parsing/jsonParser.py

The JSON parser reads one JSON document and yields records. With an
``items_path`` it first descends to that list (the role ``items_path`` played
for the API collector); without one it yields the whole document as a single
record.
"""

import json

import pytest

from parsing.jsonParser import JsonParser

pytestmark = pytest.mark.unit


def _write(tmp_path, data):
    path = tmp_path / "raw.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_items_path_yields_each_element(tmp_path):
    path = _write(tmp_path, {"national": [{"contestCode": "1"}, {"contestCode": "2"}]})

    records = list(JsonParser().parse(path, {"items_path": "national"}))

    assert records == [{"contestCode": "1"}, {"contestCode": "2"}]


def test_nested_items_path(tmp_path):
    path = _write(tmp_path, {"a": {"b": [{"x": 1}]}})

    records = list(JsonParser().parse(path, {"items_path": "a.b"}))

    assert records == [{"x": 1}]


def test_no_items_path_yields_whole_document(tmp_path):
    path = _write(tmp_path, {"a": 1, "b": 2})

    records = list(JsonParser().parse(path, {}))

    assert records == [{"a": 1, "b": 2}]


def test_missing_items_path_yields_nothing(tmp_path):
    path = _write(tmp_path, {"other": 1})

    records = list(JsonParser().parse(path, {"items_path": "national"}))

    assert records == []
