"""Unit tests for the CIA World Leaders backfill script (no DB, no network)."""

import pytest

from scripts.watchlist import backfill_cia_world_leaders as backfill

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ALBANIA", "Albania"),
        ("A ALBANIA (continued)", "Albania"),
        ("BAHAMAS, THE", "Bahamas, The"),
        ("CONGO, DEMOCRATIC REPUBLIC OF THE", "Congo, Democratic Republic of the"),
        ("COTE D’IVOIRE", "Cote d’Ivoire"),
        ("HOLY SEE (VATICAN CITY)", "Holy See (Vatican City)"),
        ("GUINEA-BISSAU", "Guinea-Bissau"),
        ("KOREA, NORTH—NDE", "Korea, North - NDE"),
        ("Korea, North—NDE", "Korea, North - NDE"),
        ("Korea, North - NDE", "Korea, North - NDE"),
    ],
)
def test_clean_country_matches_live_site_spelling(raw, expected):
    assert backfill._clean_country(raw) == expected


@pytest.fixture()
def fake_services(monkeypatch, tmp_path):
    """Replace every DB/storage call load_month makes; record what ran."""
    calls = []
    pdf = tmp_path / "month.pdf"
    pdf.write_bytes(b"%PDF")

    file_service = backfill.watchlistFileService
    monkeypatch.setattr(file_service, "calculate_file_metadata", lambda file_path: {"file_hash": "h"})
    monkeypatch.setattr(file_service, "resolve_lookup_values", lambda config: {"source_id": 1, "list_type_id": 2})
    monkeypatch.setattr(file_service, "determine_file_version", lambda **kw: "2")
    monkeypatch.setattr(file_service, "store_source_file", lambda **kw: calls.append("store") or "path")
    monkeypatch.setattr(file_service, "insert_watchlist_file", lambda **kw: calls.append("insert_file") or 99)
    monkeypatch.setattr(
        backfill.watchlistRawService,
        "process_records",
        lambda **kw: calls.append(("raw", kw["watchlist_file_id"])) or {"raw_record_count": 3},
    )
    monkeypatch.setattr(
        backfill.watchlistCoreService,
        "process_watchlist_file",
        lambda **kw: calls.append(("core", kw["watchlist_file_id"])) or {
            "new_count": 3, "updated_count": 0, "deleted_count": 0, "skipped_count": 0,
        },
    )

    def set_duplicate(status, file_id=None):
        monkeypatch.setattr(
            file_service,
            "check_duplicate",
            lambda **kw: {"duplicate_status": status, "watchlist_file_id": file_id},
        )

    return calls, pdf, set_duplicate


def test_new_month_registers_file_then_loads(fake_services):
    calls, pdf, set_duplicate = fake_services
    set_duplicate("NEW_VERSION")

    result = backfill.load_month({}, [{}], pdf)

    assert result["status"] == "LOADED"
    assert calls == ["store", "insert_file", ("raw", 99), ("core", 99)]


def test_resume_processing_reuses_file_and_inserts_raw(fake_services):
    calls, pdf, set_duplicate = fake_services
    set_duplicate("RESUME_PROCESSING", file_id=7)

    result = backfill.load_month({}, [{}], pdf)

    assert result["status"] == "RESUMED"
    assert calls == [("raw", 7), ("core", 7)]


def test_resume_normalization_skips_raw_insert(fake_services):
    calls, pdf, set_duplicate = fake_services
    set_duplicate("RESUME_NORMALIZATION", file_id=7)

    result = backfill.load_month({}, [{}], pdf)

    assert result["status"] == "RESUMED"
    assert result["raw_record_count"] is None
    assert calls == [("core", 7)]


def test_completed_month_is_skipped(fake_services):
    calls, pdf, set_duplicate = fake_services
    set_duplicate("DUPLICATE_COMPLETED", file_id=7)

    assert backfill.load_month({}, [{}], pdf) == {"status": "SKIPPED_DUPLICATE"}
    assert calls == []
