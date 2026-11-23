"""
Unit tests for services/watchlistPipeline/watchlistFileService.py

Covers the raw.watchlist_file decisions the Data Insertion Guideline pins down:

  * Duplicate Detection (guideline "raw.watchlist_file" section 5)
      Case 1 -- no existing file          -> FIRST_DOWNLOAD
      Case 2 -- existing, same hash        -> exact duplicate (further split by
                                              how far that file already got:
                                              DUPLICATE_COMPLETED / RESUME_*)
      Case 3 -- existing, different hash   -> NEW_VERSION
  * File Versioning Strategy (section 6)
      continuous  -> every new hash bumps the version number (1, 2, 3, ...)
      independent -> no logical version (None)

The DB is replaced with mocks: we patch the connection pool and the
watchlistFileRepository lookups, then assert the status/version the service
derives from what the "database" returned.
"""

import os
from unittest.mock import MagicMock

import pytest

pytest.importorskip("boto3", reason="full pipeline stack (boto3) not installed")
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")

from services.watchlistPipeline import watchlistFileService as fs  # noqa: E402

pytestmark = pytest.mark.unit


def test_meti_uses_existing_resolver_and_downloader(monkeypatch, tmp_path):
    config = {
        "source_name": "METI", "list_name": "METI_FOREIGN_END_USER_LIST",
        "download_method": "HTTPS", "file_type": "xlsx",
        "download_headers": {"User-Agent": "configured client"},
        "url_resolver": {"type": "link_text", "source_page_url": "https://www.meti.go.jp/policy/anpo/law00.html", "value": "外国ユーザーリスト"},
    }
    source = tmp_path / "current.xlsx"
    source.write_bytes(b"downloaded workbook")
    url = "https://www.meti.go.jp/policy/anpo/future.xlsx"
    resolver = MagicMock(return_value=url)
    monkeypatch.setattr(fs, "resolve_url", resolver)
    browser = MagicMock()
    monkeypatch.setattr(fs, "_collect_bypass_source", browser)
    downloader = MagicMock()
    downloader.download.return_value = str(source)
    assert fs.acquire_source_file(config, downloader) == source.resolve()
    resolver.assert_called_once_with(config)
    task = downloader.download.call_args.args[0]
    assert task.url == url
    assert task.file_type == "xlsx"
    assert task.headers == config["download_headers"]
    browser.assert_not_called()


@pytest.mark.parametrize("headers", [None, {"User-Agent": "source-client/1.0"}])
def test_https_acquisition_passes_optional_headers_to_downloader(tmp_path, headers):
    source_file = tmp_path / "listing.html"
    source_file.write_text("<html>Officials</html>", encoding="utf-8")
    downloader = MagicMock()
    downloader.download.return_value = str(source_file)
    config = {
        "source_name": "BSP",
        "list_name": "BSP_SENIOR_OFFICIALS",
        "download_method": "HTTPS",
        "url": "https://example.test/officials",
        "file_type": "html",
    }
    if headers is not None:
        config["download_headers"] = headers

    result = fs.acquire_source_file(config, downloader)

    assert result == source_file.resolve()
    task = downloader.download.call_args.args[0]
    assert task.headers == (headers or {})
    assert task.url == config["url"]
    assert task.source_name == "BSP"
    assert task.list_name == "BSP_SENIOR_OFFICIALS"


@pytest.mark.parametrize("content,require_pdf,accepted", [
    (b"%PDF-1.7\nexample", True, True),
    (b"<html>Homepage</html>", True, False),
    (b"", True, False),
    (b"<html>Other source</html>", False, True),
])
def test_optional_pdf_validation_rejects_and_removes_non_pdf_downloads(tmp_path, content, require_pdf, accepted):
    downloaded = tmp_path / "download.pdf"
    downloaded.write_bytes(content)
    downloader = MagicMock()
    downloader.download.return_value = str(downloaded)
    config = {
        "source_name": "EXAMPLE", "list_name": "ANNUAL_REGISTER",
        "download_method": "HTTPS", "url": "https://annual.test/file.pdf",
        "file_type": "pdf", "require_pdf_header": require_pdf,
        "download_timeout": 120,
    }
    if accepted:
        assert fs.acquire_source_file(config, downloader) == downloaded.resolve()
        assert downloaded.read_bytes() == content
    else:
        with pytest.raises(ValueError, match="Downloaded response is not a PDF"):
            fs.acquire_source_file(config, downloader)
        assert not downloaded.exists()
    task = downloader.download.call_args.args[0]
    assert task.timeout == 120
    assert task.url == config["url"]


def test_annual_resolver_download_and_pdf_validation_work_together(tmp_path, monkeypatch):
    from datetime import date
    import requests
    from ingestion.urlResolver import linkResolver

    clock = MagicMock()
    clock.today.return_value = date(2026, 6, 1)
    monkeypatch.setattr(linkResolver, "date", clock)
    response = requests.Response()
    response.status_code = 200
    response.url = "https://annual.test/2026"
    response._content = b'<h1>Register 2026</h1><a href="/register.pdf">Download</a>'
    get = MagicMock(return_value=response)
    monkeypatch.setattr(linkResolver.requests, "get", get)
    downloaded = tmp_path / "register.pdf"
    downloaded.write_bytes(b"%PDF-1.7\nexample")
    downloader = MagicMock()
    downloader.download.return_value = str(downloaded)
    config = {
        "source_name": "EXAMPLE", "list_name": "ANNUAL_REGISTER", "file_type": "pdf",
        "url_resolver": {
            "type": "latest_year_page", "page_url_template": "https://annual.test/{year}",
            "page_heading_template": "Register {year}", "first_year": 2026,
            "years_ahead": 0, "download_path_suffix": ".pdf", "timeout": 60,
        },
        "download_timeout": 120, "require_pdf_header": True,
    }
    assert fs.acquire_source_file(config, downloader) == downloaded.resolve()
    assert downloader.download.call_args.args[0].url == "https://annual.test/register.pdf"
    get.assert_called_once_with("https://annual.test/2026", timeout=60)


def _patch_latest_file(monkeypatch, return_value):
    monkeypatch.setattr(fs, "connection_pool", MagicMock())
    monkeypatch.setattr(
        fs.watchlistFileRepository,
        "find_latest_file",
        MagicMock(return_value=return_value),
    )


# --- Duplicate detection (section 5) ----------------------------------------

def test_first_download_when_no_existing_file(monkeypatch):
    _patch_latest_file(monkeypatch, None)

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="abc")

    assert result["duplicate_status"] == "FIRST_DOWNLOAD"


def test_different_hash_is_a_new_version(monkeypatch):
    _patch_latest_file(monkeypatch, {"file_hash": "OLD-HASH"})

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="NEW-HASH")

    assert result["duplicate_status"] == "NEW_VERSION"


def test_same_hash_already_normalized_is_completed_duplicate(monkeypatch):
    _patch_latest_file(monkeypatch, {
        "id": 50, "file_hash": "SAME", "file_version": "3", "storage_path": "p",
        "status": "PARSED", "has_raw_payloads": True, "normalization_completed": True,
    })

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="SAME")

    assert result["duplicate_status"] == "DUPLICATE_COMPLETED"
    assert result["watchlist_file_id"] == 50


def test_same_hash_with_raw_but_not_normalized_resumes_normalization(monkeypatch):
    _patch_latest_file(monkeypatch, {
        "id": 50, "file_hash": "SAME", "file_version": "3", "storage_path": "p",
        "status": "PARSED", "has_raw_payloads": True, "normalization_completed": False,
    })

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="SAME")

    assert result["duplicate_status"] == "RESUME_NORMALIZATION"


def test_same_hash_not_yet_parsed_resumes_processing(monkeypatch):
    _patch_latest_file(monkeypatch, {
        "id": 50, "file_hash": "SAME", "file_version": "3", "storage_path": "p",
        "status": "DOWNLOADED", "has_raw_payloads": False, "normalization_completed": False,
    })

    result = fs.check_duplicate(source_id=1, list_type_id=2, file_hash="SAME")

    assert result["duplicate_status"] == "RESUME_PROCESSING"


# --- File versioning (section 6) --------------------------------------------

def test_independent_lists_have_no_version(monkeypatch):
    result = fs.determine_file_version(
        config={"versioning_strategy": "independent"},
        duplicate_status="FIRST_DOWNLOAD",
        source_id=1,
        list_type_id=2,
    )
    assert result is None


def test_first_download_is_version_one(monkeypatch):
    result = fs.determine_file_version(
        config={"versioning_strategy": "continuous"},
        duplicate_status="FIRST_DOWNLOAD",
        source_id=1,
        list_type_id=2,
    )
    assert result == "1"


def test_new_version_increments_the_latest(monkeypatch):
    monkeypatch.setattr(fs, "connection_pool", MagicMock())
    monkeypatch.setattr(
        fs.watchlistFileRepository,
        "find_latest_file_version",
        MagicMock(return_value="3"),
    )

    result = fs.determine_file_version(
        config={"versioning_strategy": "continuous"},
        duplicate_status="NEW_VERSION",
        source_id=1,
        list_type_id=2,
    )
    assert result == "4"


def test_non_numeric_latest_version_raises(monkeypatch):
    monkeypatch.setattr(fs, "connection_pool", MagicMock())
    monkeypatch.setattr(
        fs.watchlistFileRepository,
        "find_latest_file_version",
        MagicMock(return_value="not-a-number"),
    )

    with pytest.raises(ValueError, match="Invalid latest file version"):
        fs.determine_file_version(
            config={"versioning_strategy": "continuous"},
            duplicate_status="NEW_VERSION",
            source_id=1,
            list_type_id=2,
        )
