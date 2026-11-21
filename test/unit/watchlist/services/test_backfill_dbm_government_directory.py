"""DBM backfill orchestration: no database, storage, downloads or real PDFs."""

from copy import deepcopy
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest
import requests

from ingestion.urlResolver import linkResolver
from scripts.watchlist import backfill_dbm_government_directory as backfill


pytestmark = pytest.mark.unit


def test_edition_config_keeps_assignment_and_versioning_contract():
    original = deepcopy(backfill.WATCHLIST_CONFIGS[backfill.DBM_KEY])
    config = backfill.build_edition_config(2020)
    assert config["url_resolver"]["first_year"] == 2020
    assert config["download_filename"] == "dbm-government-directory-2020.pdf"
    assert config["versioning_strategy"] == "continuous"
    assert config["external_id_path"] == "source_record_id"
    assert config["preprocessing"] == original["preprocessing"]
    assert backfill.WATCHLIST_CONFIGS[backfill.DBM_KEY] == original


@pytest.fixture
def fetched(monkeypatch, tmp_path):
    path = tmp_path / "dbm-government-directory-2020.pdf"
    path.write_bytes(b"%PDF-test")
    records = [{"listing": i} for i in range(5000)]
    resolve = MagicMock(return_value="https://dbm.test/2020.pdf")
    acquire = MagicMock(return_value=path)
    parse = MagicMock(return_value=records)
    monkeypatch.setattr(backfill, "resolve_latest_year_page", resolve)
    monkeypatch.setattr(backfill.watchlistFileService, "acquire_source_file", acquire)
    monkeypatch.setattr(backfill.DbmDirectoryParser, "parse", parse)
    return path, records, resolve, acquire, parse


def test_fetch_pins_year_and_preserves_source_provenance(fetched):
    path, records, resolve, acquire, parse = fetched
    config, actual, actual_path = backfill.fetch_edition(2020)
    resolve.assert_called_once_with(backfill.build_edition_config(2020)["url_resolver"], last_year=2020)
    assert "url_resolver" not in config
    assert config["url"] == "https://dbm.test/2020.pdf"
    acquire.assert_called_once_with(config, backfill.downloader)
    parse.assert_called_once_with(path, config)
    assert actual == records
    assert actual_path == path


def test_confirmed_missing_edition_does_not_download(fetched):
    _, _, resolve, acquire, _ = fetched
    resolve.side_effect = FileNotFoundError("No edition page")
    assert backfill.fetch_edition(2020) is None
    acquire.assert_not_called()


@pytest.mark.parametrize("error", [ValueError("wrong heading"), requests.HTTPError("503")])
def test_discovery_failure_is_not_treated_as_absent(fetched, error):
    _, _, resolve, acquire, _ = fetched
    resolve.side_effect = error
    with pytest.raises(type(error)):
        backfill.fetch_edition(2020)
    acquire.assert_not_called()


def test_partial_pdf_is_rejected_before_loading(fetched):
    _, _, _, _, parse = fetched
    parse.return_value = [{"listing": 1}]
    with pytest.raises(ValueError, match="partial snapshot"):
        backfill.fetch_edition(2020)


def test_dry_run_writes_raw_jsonl_without_loader(monkeypatch, tmp_path):
    loader = MagicMock(side_effect=AssertionError("No DB writes during dry run"))
    monkeypatch.setattr(backfill, "load_snapshot", loader)
    result = backfill.run_edition(2020, ({}, [{"name": "José"}], tmp_path / "source.pdf"),
                                 dry_run=True, jsonl_dir=tmp_path / "output")
    assert result["status"] == "PARSED"
    assert (tmp_path / "output/2020.jsonl").read_text() == '{"name": "José"}\n'
    loader.assert_not_called()


def test_load_reuses_cia_snapshot_loader(monkeypatch, tmp_path):
    loader = MagicMock(return_value={"status": "RESUMED"})
    monkeypatch.setattr(backfill, "load_snapshot", loader)
    config, records, path = {"versioning_strategy": "continuous"}, [{}], tmp_path / "file.pdf"
    assert backfill.run_edition(2020, (config, records, path), dry_run=False,
                                jsonl_dir=tmp_path) == {"status": "RESUMED"}
    loader.assert_called_once_with(config, records, path)


@pytest.mark.parametrize("dry_run", [False, True])
def test_main_processes_oldest_to_latest(monkeypatch, tmp_path, dry_run):
    seen = []
    preflight = MagicMock()
    monkeypatch.setattr(backfill, "validate_start_year", preflight)
    monkeypatch.setattr(backfill, "load_edition", lambda year, **kw: seen.append((year, kw["dry_run"])))
    args = ["--start", "2018", "--end", "2020", "--jsonl-dir", str(tmp_path)]
    if dry_run:
        args.append("--dry-run")
    assert backfill.main(args) == 0
    assert seen == [(year, dry_run) for year in (2018, 2019, 2020)]
    assert preflight.call_args_list == ([] if dry_run else [((2018,), {})])


def test_retry_failed_fetch_and_stop_before_later_editions(monkeypatch, tmp_path):
    fetch = MagicMock(side_effect=RuntimeError("network"))
    monkeypatch.setattr(backfill, "fetch_edition", fetch)
    monkeypatch.setattr(backfill, "sleep", lambda seconds: None)
    assert backfill.main(["--dry-run", "--start", "2018", "--end", "2020",
                          "--jsonl-dir", str(tmp_path)]) == 1
    assert [call.args[0] for call in fetch.call_args_list] == [2018, 2018, 2018]


def test_failed_preflight_stops_before_download_or_load(monkeypatch):
    monkeypatch.setattr(backfill, "validate_start_year", MagicMock(side_effect=ValueError("newer edition")))
    load = MagicMock()
    monkeypatch.setattr(backfill, "load_edition", load)
    assert backfill.main(["--start", "2018", "--end", "2020"]) == 1
    load.assert_not_called()


def test_retry_load_reuses_fetched_pdf(monkeypatch, tmp_path):
    fetched = ({}, [{}], tmp_path / "source.pdf")
    fetch = MagicMock(side_effect=[RuntimeError("network"), fetched])
    run = MagicMock(side_effect=[RuntimeError("DB unavailable"), {"status": "RESUMED"}])
    monkeypatch.setattr(backfill, "fetch_edition", fetch)
    monkeypatch.setattr(backfill, "run_edition", run)
    monkeypatch.setattr(backfill, "sleep", lambda seconds: None)
    assert backfill.load_edition(2020, dry_run=False, jsonl_dir=tmp_path) == {"status": "RESUMED"}
    assert fetch.call_count == 2
    assert run.call_count == 2


def test_absent_year_continues_without_loading(monkeypatch, tmp_path):
    monkeypatch.setattr(backfill, "fetch_edition", lambda year: None)
    run = MagicMock()
    monkeypatch.setattr(backfill, "run_edition", run)
    assert backfill.load_edition(2020, dry_run=False, jsonl_dir=tmp_path) is None
    run.assert_not_called()


@pytest.mark.parametrize("args", [["--start", "2017"], ["--start", "2022", "--end", "2020"]])
def test_invalid_ranges_fail_before_fetch(monkeypatch, args):
    fetch = MagicMock()
    monkeypatch.setattr(backfill, "fetch_edition", fetch)
    with pytest.raises(SystemExit) as error:
        backfill.main(args)
    assert error.value.code == 2
    fetch.assert_not_called()


@pytest.mark.parametrize("latest,start,rejected", [
    (None, 2018, False),
    (("dbm-government-directory-2020.pdf", "https://dbm.test/file.pdf"), 2020, False),
    (("dbm-government-directory-2020.pdf", None), 2019, True),
    (("download.pdf", "https://www.dbm.gov.ph/2022-english-version"), 2018, True),
    (("unknown.pdf", None), 2018, True),
])
def test_existing_newer_or_unidentifiable_edition_blocks_replay(monkeypatch, latest, start, rejected):
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = latest
    monkeypatch.setattr(backfill, "get_db_connection", lambda: nullcontext(connection))
    monkeypatch.setattr(backfill.watchlistFileService, "resolve_lookup_values",
                        lambda config: {"source_id": 1, "list_type_id": 2})
    if rejected:
        with pytest.raises(ValueError):
            backfill.validate_start_year(start)
    else:
        backfill.validate_start_year(start)


def test_bounded_resolver_checks_only_the_requested_year(monkeypatch):
    response = requests.Response()
    response.status_code = 404
    response.url = "https://dbm.test/2020"
    get = MagicMock(return_value=response)
    monkeypatch.setattr(linkResolver.requests, "get", get)
    config = backfill.build_edition_config(2020)["url_resolver"]
    with pytest.raises(FileNotFoundError):
        linkResolver.resolve_latest_year_page(config, last_year=2020)
    get.assert_called_once_with(config["page_url_template"].format(year=2020), timeout=60)
