import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from infrastructure.storage import seaweedClient
from services.adverseMediaPipeline import mediaAcquisitionService as acquisition
from services.adverseMediaPipeline.mediaRawRecordService import MediaRawRecordService
from utils.hashing import calculate_file_hash


pytestmark = pytest.mark.unit
URL = "https://court.example/cases/"
PDF = "https://court.example/order.pdf"
CONFIG = {"parser": {"type": "json"}, "document_download": {
    "parser": {"type": "pdf", "mode": "text", "require_text": True},
}}


def _record(tmp_path):
    row = tmp_path / "row.html"
    row.write_text("<table><tr><td>C-1</td></tr></table>", encoding="utf-8")
    pdf = tmp_path / "order.pdf"
    pdf.write_bytes(b"%PDF-original")
    return {
        "record_key": "entry", "retry_listing": True, "attachment_manifest": True,
        "detail_file_path": str(row), "extracted": {
            "CaseNumber": "C-1", "SourceURL": URL, "PDFURL": PDF,
            "DocumentFiles": [{"url": PDF, "local_path": str(pdf)}],
            "DocumentFilePaths": [str(pdf)], "DocumentErrors": [],
        },
    }


def _persist(monkeypatch, record, *, fail_attachment=False):
    calls = []

    def metadata(file_path, file_url):
        return {"file_path": str(file_path), "file_url": file_url}

    def register(**kwargs):
        file = kwargs["file_metadata"]
        calls.append(file)
        if fail_attachment and file["file_url"] == PDF:
            raise OSError("storage unavailable")
        return {
            "media_file_id": len(calls), "file_hash": calculate_file_hash(file["file_path"]),
            "storage_path": f"durable/{len(calls)}", "status": "DOWNLOADED", "is_duplicate": False,
        }

    monkeypatch.setattr(acquisition.MediaFileService, "build_file_metadata", metadata)
    monkeypatch.setattr(acquisition.MediaRawService, "register_file", register)
    result = acquisition.MediaAcquisitionService._persist_record(
        record=record, source_id=1, dataset_id=2, source_name="COURT", dataset_name="ENTRIES",
        download_method="BYPASS", acquisition_url=URL,
    )
    return result, calls


def test_manifest_contains_source_evidence_and_durable_references_without_local_paths(tmp_path, monkeypatch):
    record = _record(tmp_path)
    result, calls = _persist(monkeypatch, record)
    manifest_path = Path(result["detail_file_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [call["file_url"] for call in calls] == [PDF, URL]
    assert manifest["SourceRowHTML"] == Path(record["detail_file_path"]).read_text(encoding="utf-8")
    assert manifest["CaseNumber"] == "C-1"
    assert manifest["DocumentFiles"] == [{
        "url": PDF, "media_file_id": 1,
        "file_hash": calculate_file_hash(record["extracted"]["DocumentFiles"][0]["local_path"]),
        "storage_path": "durable/1",
    }]
    assert "DocumentFilePaths" not in manifest
    assert str(tmp_path) not in manifest_path.read_text(encoding="utf-8")
    assert list(tmp_path.glob("*.json")) == [manifest_path]
    assert not list(tmp_path.glob("*.tmp"))
    assert result["retry_listing"] is True


def test_attachment_storage_failure_still_persists_metadata_and_records_error(tmp_path, monkeypatch):
    result, calls = _persist(monkeypatch, _record(tmp_path), fail_attachment=True)
    manifest = json.loads(Path(result["detail_file_path"]).read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert manifest["CaseNumber"] == "C-1"
    assert manifest["DocumentErrors"][0]["stage"] == "storage"
    assert manifest["DocumentFiles"] == [{"url": PDF}]
    assert not result["failed"]


@pytest.mark.parametrize("preloaded", [True, False])
def test_stored_manifest_replays_missing_local_pdf_once_through_existing_parser(tmp_path, monkeypatch, preloaded):
    record = _record(tmp_path)
    original_bytes = Path(record["extracted"]["DocumentFiles"][0]["local_path"]).read_bytes()
    persisted, _ = _persist(monkeypatch, record)
    Path(record["extracted"]["DocumentFiles"][0]["local_path"]).unlink()
    retrieved = []

    def download(storage_path, destination_path):
        retrieved.append(storage_path)
        Path(destination_path).write_bytes(original_bytes)
        return str(destination_path)

    monkeypatch.setattr(seaweedClient, "download_file", download)
    parser = MagicMock()
    parser.parse.return_value = iter([{"BodyText": "Parsed order"}])
    import services.adverseMediaPipeline.mediaRawRecordService as raw
    real_factory = raw.create_parser
    monkeypatch.setattr(raw, "create_parser", lambda file_type: parser if file_type == "pdf" else real_factory(file_type))
    service = MediaRawRecordService()
    loaded = service.extract(CONFIG, source_file_path=persisted["detail_file_path"])
    assert retrieved == []
    parser.parse.assert_not_called()
    acquired = ({"extracted": loaded[0], "detail_file_path": None} if preloaded else
                {"detail_file_path": persisted["detail_file_path"]})
    [processed] = service.extract_acquired_record(CONFIG, acquired)
    assert processed["CaseNumber"] == "C-1"
    assert processed["DocumentBodyText"] == f"Document: {PDF}\nParsed order"
    assert processed["DocumentErrors"] == []
    assert retrieved == ["durable/1"]
    parser.parse.assert_called_once()


def test_optional_attachment_uses_real_pdf_parser_and_preserves_table_metadata():
    fixture = Path(__file__).resolve().parents[3] / "fixtures/parsing/sec_advisory_text.pdf"
    [processed] = MediaRawRecordService().extract_acquired_record(CONFIG, {"extracted": {
        "CaseNumber": "C-1", "DocumentFiles": [{"url": PDF, "local_path": str(fixture)}],
    }})
    assert processed["CaseNumber"] == "C-1"
    assert "EXNESS GLOBAL LIMITED" in processed["DocumentBodyText"]
    assert len(processed["DocumentTexts"]) == 1
    assert processed["DocumentErrors"] == []


@pytest.mark.parametrize("failure", ["missing", "hash", "parse"])
def test_attachment_processing_failures_preserve_metadata_and_leave_input_unchanged(tmp_path, monkeypatch, failure):
    record = _record(tmp_path)
    extracted = record["extracted"]
    if failure == "missing":
        Path(extracted["DocumentFiles"][0]["local_path"]).unlink()
    elif failure == "hash":
        extracted["DocumentFiles"][0]["file_hash"] = "incorrect"
    parser = MagicMock()
    parser.parse.side_effect = ValueError("unreadable PDF")
    monkeypatch.setattr("services.adverseMediaPipeline.mediaRawRecordService.create_parser", lambda **_: parser)
    original = deepcopy(extracted)
    [processed] = MediaRawRecordService().extract_acquired_record(CONFIG, record)
    assert processed["CaseNumber"] == "C-1"
    assert processed["DocumentErrors"][0]["stage"] == "parse"
    assert processed["DocumentBodyText"] == ""
    assert extracted == original
    assert parser.parse.call_count == (1 if failure == "parse" else 0)
