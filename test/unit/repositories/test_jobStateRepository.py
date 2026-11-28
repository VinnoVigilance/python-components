"""Unit tests for repositories/jobStateRepository.py (JSON state files between runs)."""

import csv

import pytest

from repositories import jobStateRepository as repo

pytestmark = pytest.mark.unit


def test_load_returns_none_when_no_state():
    assert repo.load_state("watchlist", "NCA") is None


def test_save_then_load_round_trips(isolated_job_state):
    path = repo.save_state("watchlist", "NCA", {"attempts": 1, "missing": []})

    assert path == isolated_job_state / "watchlist" / "NCA.json"
    assert repo.load_state("watchlist", "NCA") == {"attempts": 1, "missing": []}
    assert not path.with_suffix(".tmp").exists()


def test_delete_removes_state_and_ignores_missing_file():
    repo.save_state("media", "SEC", {"items": {}})

    repo.delete_state("media", "SEC")
    repo.delete_state("media", "SEC")

    assert repo.load_state("media", "SEC") is None


def test_list_state_names_is_sorted_and_per_kind():
    repo.save_state("watchlist", "UN", {})
    repo.save_state("watchlist", "CFTC", {})
    repo.save_state("media", "SEC", {})

    assert repo.list_state_names("watchlist") == ["CFTC", "UN"]
    assert repo.list_state_names("media") == ["SEC"]
    assert repo.list_state_names("other") == []


def _read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_export_pending_csv_lists_watchlist_missing_pages(tmp_path):
    repo.save_state("watchlist", "NCA", {
        "attempts": 2,
        "missing": [
            {"record_id": "7", "detail_url": "https://x/7", "error": "timeout"},
            {"record_id": "9", "detail_url": "https://x/9", "error": "HTTP 404", "permanent": True},
        ],
    })
    path = tmp_path / "pending.csv"

    assert repo.export_pending_csv("watchlist", path) == 2

    rows = _read_csv(path)
    assert rows[0] == {
        "source": "NCA", "key": "7", "url": "https://x/7",
        "attempts": "2", "error": "timeout", "broken_on_source": "False",
        "error_category": "", "error_type": "", "http_status": "",
    }
    assert rows[1]["broken_on_source"] == "True"


def test_export_pending_csv_lists_media_todo_items(tmp_path):
    repo.save_state("media", "SEC", {"items": {
        "k1": {"record_key": "k1", "detail_url": "https://x/a", "attempts": 3, "error": "boom"},
    }})
    path = tmp_path / "pending.csv"

    assert repo.export_pending_csv("media", path) == 1
    assert _read_csv(path)[0]["key"] == "k1"
    assert _read_csv(path)[0]["attempts"] == "3"


@pytest.mark.parametrize("kind", ["watchlist", "media"])
def test_failure_diagnostics_survive_state_and_csv_export(tmp_path, kind):
    item = {"record_id": "7", "record_key": "7", "detail_url": "https://x/7",
            "error": "HTTP 404", "permanent": True, "error_category": "HTTP_NOT_FOUND",
            "error_type": "HttpError", "http_status": 404, "attempts": 2}
    state = {"missing": [item]} if kind == "watchlist" else {"items": {"7": item}}
    repo.save_state(kind, "SOURCE", state)
    assert repo.load_state(kind, "SOURCE") == state
    path = tmp_path / "pending.csv"

    assert repo.export_pending_csv(kind, path) == 1

    [row] = _read_csv(path)
    assert row == {"source": "SOURCE", "key": "7", "url": "https://x/7", "attempts": "2",
                   "error": "HTTP 404", "broken_on_source": "True",
                   "error_category": "HTTP_NOT_FOUND", "error_type": "HttpError", "http_status": "404"}


def test_export_pending_csv_writes_nothing_when_empty(tmp_path):
    path = tmp_path / "pending.csv"

    assert repo.export_pending_csv("watchlist", path) == 0
    assert not path.exists()
