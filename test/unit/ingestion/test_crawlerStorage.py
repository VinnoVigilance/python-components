"""File integrity checks for crawler publication storage."""

from pathlib import Path

import pytest

from ingestion.crawler.storage import CrawlerStorage


pytestmark = pytest.mark.unit


def test_binary_storage_preserves_bytes_and_replaces_existing_destination(tmp_path):
    storage = CrawlerStorage("SOURCE", "DATASET", str(tmp_path), detail_directory="")
    payload = b"%PDF-1.7\n\x00\xff\x80\n"

    path = Path(storage.save_detail_file("abc123", payload, "pdf"))

    assert path.is_absolute()
    assert path.parent == storage.detail_path.resolve()
    assert path.read_bytes() == payload
    assert not path.with_suffix(".pdf.tmp").exists()

    storage.save_detail_file("abc123", b"%PDF-replacement", "pdf")
    assert path.read_bytes() == b"%PDF-replacement"


@pytest.mark.parametrize("record_id,extension", [
    ("../outside", "pdf"), ("abc", "../pdf"), ("", "pdf"), ("abc", ""),
])
def test_invalid_filename_components_write_nothing(tmp_path, record_id, extension):
    storage = CrawlerStorage("SOURCE", "DATASET", str(tmp_path))

    with pytest.raises(ValueError):
        storage.save_detail_file(record_id, b"content", extension)

    assert not list(storage.detail_path.iterdir())


def test_interrupted_write_preserves_existing_publication(tmp_path, monkeypatch):
    storage = CrawlerStorage("SOURCE", "DATASET", str(tmp_path))
    path = Path(storage.save_detail_file("abc123", b"original", "pdf"))

    def interrupted_write(temporary, content):
        with temporary.open("wb") as handle:
            handle.write(content[:2])
        raise OSError("interrupted write")

    monkeypatch.setattr(Path, "write_bytes", interrupted_write)

    with pytest.raises(OSError, match="interrupted write"):
        storage.save_detail_file("abc123", b"replacement", "pdf")

    assert path.read_bytes() == b"original"


def test_existing_html_storage_preserves_unicode(tmp_path):
    storage = CrawlerStorage("SOURCE", "DATASET", str(tmp_path))
    html = "<p>Philippines \u00f1</p>"

    path = Path(storage.save_detail_html("html1", html))

    assert path.read_text(encoding="utf-8") == html
