"""PDF save actions use browser downloads without bypassing payload validation."""

import base64
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ingestion.bypassCollector.actions.saveAction import SavePdfAction


pytestmark = pytest.mark.unit

PDF = (Path(__file__).resolve().parents[2] / "fixtures" / "parsing" / "dnfbp.pdf").read_bytes()
URL = "https://example.org/list.pdf"


@pytest.fixture
def work_dir():
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        yield Path(directory)


def _setup(tmp_path):
    browser = tmp_path / "browser"
    browser.mkdir()
    engine = SimpleNamespace(downloadDirectory=browser)
    context = {
        "url": URL,
        "source_name": "PNP",
        "list_name": "PNP_PRO9_MOST_WANTED_2024",
        "outputDir": tmp_path / "saved",
    }
    action = {
        "url": URL,
        "download_mode": "navigation",
        "filename_pattern": "source.pdf",
        "max_size_bytes": len(PDF) + 1,
        "timeout_seconds": 1,
    }
    return browser, engine, context, action


def test_navigation_pdf_saves_validated_browser_file(work_dir):
    tmp_path = work_dir
    browser, engine, context, action = _setup(tmp_path)
    (browser / "download.pdf").write_bytes(PDF)

    assert SavePdfAction().execute(action, context, engine)
    saved = Path(context["savedFile"])
    assert saved.read_bytes() == PDF
    assert saved == tmp_path / "saved" / "source.pdf"


@pytest.mark.parametrize("case", ["invalid", "oversized"])
def test_navigation_pdf_rejects_invalid_or_oversized_download(work_dir, case):
    tmp_path = work_dir
    browser, engine, context, action = _setup(tmp_path)
    payload = b"<html>blocked</html>" if case == "invalid" else PDF
    (browser / "download.pdf").write_bytes(payload)
    if case == "oversized":
        action["max_size_bytes"] = len(PDF) - 1

    assert not SavePdfAction().execute(action, context, engine)
    assert "savedFile" not in context
    assert not (tmp_path / "saved" / "source.pdf").exists()


def test_navigation_pdf_requires_the_collector_url(work_dir):
    tmp_path = work_dir
    browser, engine, context, action = _setup(tmp_path)
    (browser / "download.pdf").write_bytes(PDF)
    action["url"] = "https://example.org/other.pdf"

    assert not SavePdfAction().execute(action, context, engine)
    assert "savedFile" not in context


def test_existing_fetch_mode_remains_default(work_dir):
    tmp_path = work_dir
    _, engine, context, action = _setup(tmp_path)
    action.pop("download_mode")
    result = {
        "ok": True,
        "status": 200,
        "size": len(PDF),
        "base64": base64.b64encode(PDF).decode("ascii"),
    }

    with patch.object(SavePdfAction, "_fetch_pdf", return_value=result) as fetch:
        assert SavePdfAction().execute(action, context, engine)

    fetch.assert_called_once_with(engine, URL, 1, len(PDF) + 1)
    assert Path(context["savedFile"]).read_bytes() == PDF
