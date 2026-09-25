import json

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
