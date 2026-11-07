"""Multi-feed completeness and atomic publication without network access."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from ingestion.apiCollector import collector
from ingestion.apiCollector.models import ApiCollectorTask

pytestmark = pytest.mark.unit


def _task(tmp_path):
    task = ApiCollectorTask.from_config({
        "source_name": "TEST", "list_name": "TEST-NOTICES",
        "url": "https://example.test/persons", "download_dir": str(tmp_path),
        "api_config": {
            "write_mode": "list_detail",
            "endpoints": [
                {"url": "https://example.test/persons",
                 "record_fields": {"record_type": "Individual"}},
                {"url": "https://example.test/entities",
                 "record_fields": {"record_type": "Entity"}},
            ],
            "pagination": {"type": "page", "size_param": "resultPerPage"},
            "record_shape": {"id_path": "entity_id"},
            "detail": {"url_path": "_links.self.href"},
        },
    })
    return replace(task, download_dir=str(tmp_path))


def _stub(record_id):
    return {"entity_id": record_id,
            "_links": {"self": {"href": "https://example.test/" + record_id}}}


def test_combines_feeds_and_keeps_endpoint_identity(tmp_path, monkeypatch):
    task = _task(tmp_path)
    monkeypatch.setattr(collector, "_get_json_with_retry", Mock(return_value={"total": 1}))
    pages = Mock(side_effect=[iter([[_stub("person-1")]]), iter([[_stub("entity-1")]])])
    monkeypatch.setattr(collector, "_iter_pages", pages)
    combined = [item for page in collector._iter_endpoint_pages(task, object()) for item in page]
    assert [(r["entity_id"], r["record_type"]) for r in combined] == [
        ("person-1", "Individual"), ("entity-1", "Entity"),
    ]
    assert all(not call.args[0].endpoints for call in pages.call_args_list)


@pytest.mark.parametrize("total", [None, "1", -1, True])
def test_rejects_invalid_endpoint_total(tmp_path, monkeypatch, total):
    monkeypatch.setattr(collector, "_get_json_with_retry", Mock(return_value={"total": total}))
    with pytest.raises(ValueError, match="Invalid endpoint total"):
        list(collector._iter_endpoint_pages(_task(tmp_path), object()))


def test_rejects_incomplete_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "_get_json_with_retry", Mock(return_value={"total": 2}))
    monkeypatch.setattr(collector, "_iter_pages", Mock(return_value=iter([[_stub("one")]])))
    with pytest.raises(RuntimeError, match="collected 1 of 2"):
        list(collector._iter_endpoint_pages(_task(tmp_path), object()))


def test_rejects_duplicate_ids_across_feeds(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "_get_json_with_retry", Mock(return_value={"total": 1}))
    monkeypatch.setattr(collector, "_iter_pages", Mock(side_effect=[
        iter([[_stub("same")]]), iter([[_stub("same")]]),
    ]))
    with pytest.raises(ValueError, match="Duplicate record ID across endpoints"):
        list(collector._iter_endpoint_pages(_task(tmp_path), object()))


@pytest.mark.parametrize("fails", [False, True])
def test_overview_is_published_only_after_profiles_complete(tmp_path, monkeypatch, fails):
    monkeypatch.setattr(collector, "_iter_endpoint_pages",
                        Mock(return_value=iter([[_stub("2026/102")]])))
    def fetch(task, overview, members, transport):
        assert overview.name.endswith(".jsonl.partial")
        assert not overview.with_suffix("").exists()
        if fails:
            raise RuntimeError("profile unavailable")
        return 1
    monkeypatch.setattr(collector, "_fetch_profiles", fetch)
    task = _task(tmp_path)
    if fails:
        with pytest.raises(RuntimeError, match="profile unavailable"):
            collector._collect_list_detail(task, object(), datetime(2026, 10, 24))
        assert not list(tmp_path.rglob("*.jsonl"))
        assert list(tmp_path.rglob("*.jsonl.partial"))
    else:
        path = collector._collect_list_detail(task, object(), datetime(2026, 10, 24))
        assert json.loads(Path(path).read_text(encoding="utf-8"))["entity_id"] == "2026/102"
        assert not list(tmp_path.rglob("*.partial"))


def test_profile_id_mismatch_is_not_saved(tmp_path, monkeypatch):
    overview = tmp_path / "overview.jsonl"
    overview.write_text(json.dumps(_stub("2026/102")) + "\n", encoding="utf-8")
    members = tmp_path / "members"
    monkeypatch.setattr(collector, "_get_json_with_retry",
                        Mock(return_value={"entity_id": "wrong"}))
    with pytest.raises(ValueError, match="Profile ID does not match"):
        collector._fetch_profiles(_task(tmp_path), overview, members, object())
    assert not list(members.glob("*.json"))
