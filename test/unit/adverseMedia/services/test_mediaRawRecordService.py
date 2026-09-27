import json
import shutil
from pathlib import Path

import pytest

from services.adverseMediaPipeline.mediaRawRecordService import (
    MediaRawRecordService,
)


pytestmark = pytest.mark.unit


def test_extracts_media_fields_from_saved_raw_html(
    tmp_path,
):
    raw_file = tmp_path / "101.html"
    raw_file.write_text(
        "<html><h1>DOJ Title</h1>"
        "<article>DOJ body</article></html>",
        encoding="utf-8",
    )

    records = MediaRawRecordService().extract(
        source_config={
            "url": "https://example.test/news",
            "parser": {"type": "html"},
            "discovery": {"policy": "stop_after_known"},
            "extraction": {
                "SourceURL": {
                    "source": "response_url",
                },
                "SourceRecordId": {
                    "source": "response_url",
                    "extraction": {
                        "strategy": "regex",
                        "pattern": r"[?&]newsid=([^&]+)",
                    },
                },
                "Title": {
                    "selector": "h1",
                    "output": "text",
                },
                "BodyText": {
                    "selector": "article",
                    "output": "text",
                },
            },
        },
        source_file_path=raw_file,
        source_url=(
            "https://example.test/news?newsid=101"
        ),
    )

    assert records == [
        {
            "SourceURL": (
                "https://example.test/news?newsid=101"
            ),
            "SourceRecordId": "101",
            "Title": "DOJ Title",
            "BodyText": "DOJ body",
        }
    ]


def test_extracts_one_api_record_from_saved_raw_json(
    tmp_path,
):
    raw_file = tmp_path / "42.json"
    raw_file.write_text(
        json.dumps(
            {
                "id": 42,
                "title": "API title",
            }
        ),
        encoding="utf-8",
    )

    records = MediaRawRecordService().extract(
        source_config={
            "parser": {"type": "json"},
        },
        source_file_path=raw_file,
    )

    assert records == [
        {
            "id": 42,
            "title": "API title",
        }
    ]


SEC_PDF = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "parsing"
    / "sec_advisory_text.pdf"
)

PDF_SOURCE = {
    "parser": {"type": "pdf", "mode": "text"},
}

PAGE_FIELDS = {
    "SourceURL": "https://www.sec.gov.ph/advisories-2025/exness/",
    "SourceRecordId": "advisories-2025/exness",
    "Title": "EXNESS GLOBAL LIMITED",
    "date.originalValue": "2026-01-05T02:00:00+00:00",
    "DocumentUrls": ["https://www.sec.gov.ph/wp-content/uploads/exness.pdf"],
}


def _stored_pdf(tmp_path):
    pdf_path = tmp_path / "exness_0.pdf"
    shutil.copy(SEC_PDF, pdf_path)
    return str(pdf_path)


def test_stored_pdf_text_is_added_to_the_page_fields(
    tmp_path,
):
    records = MediaRawRecordService().extract_acquired_record(
        source_config=PDF_SOURCE,
        acquired_record={
            "extracted": PAGE_FIELDS,
            "detail_file_path": _stored_pdf(tmp_path),
        },
    )

    assert len(records) == 1
    record = records[0]
    assert {key: record[key] for key in PAGE_FIELDS} == PAGE_FIELDS
    assert "EXNESS GLOBAL LIMITED" in record["BodyText"]
    assert PAGE_FIELDS.get("BodyText") is None


def test_html_source_does_not_read_the_stored_file(
    tmp_path,
):
    records = MediaRawRecordService().extract_acquired_record(
        source_config={"parser": {"type": "html"}},
        acquired_record={
            "extracted": {"Title": "DOJ Title"},
            "detail_file_path": str(tmp_path / "missing.html"),
        },
    )

    assert records == [{"Title": "DOJ Title"}]


def test_reprocess_without_page_fields_parses_only_the_pdf(
    tmp_path,
):
    records = MediaRawRecordService().extract_acquired_record(
        source_config=PDF_SOURCE,
        acquired_record={
            "extracted": None,
            "detail_file_path": _stored_pdf(tmp_path),
        },
    )

    assert len(records) == 1
    assert list(records[0]) == ["BodyText"]
    assert "EXNESS GLOBAL LIMITED" in records[0]["BodyText"]


def test_process_acquired_record_runs_preprocessing_on_the_merged_record(
    tmp_path,
):
    records = MediaRawRecordService().process_acquired_record(
        source_config={
            **PDF_SOURCE,
            "preprocessing": [
                {
                    "handler": "build_url_from_template",
                    "level": "record",
                    "config": {
                        "output_field": "RecordLink",
                        "template": "https://example.test/{SourceRecordId}",
                    },
                }
            ],
        },
        acquired_record={
            "extracted": PAGE_FIELDS,
            "detail_file_path": _stored_pdf(tmp_path),
        },
    )

    assert records[0]["RecordLink"] == (
        "https://example.test/advisories-2025/exness"
    )
    assert "EXNESS GLOBAL LIMITED" in records[0]["BodyText"]
