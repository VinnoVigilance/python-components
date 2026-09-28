"""Unit tests for the list_only media strategy: one record per listing-table row."""

import asyncio
from unittest.mock import MagicMock

import pytest

from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.spiders.savedHtmlMediaSpider import SavedHtmlMediaSpider
from ingestion.crawler.storage import CrawlerStorage


pytestmark = pytest.mark.unit

URL = "https://www.adb.org/who-we-are/integrity/case-summaries"

LISTING = """
<html><body>
<table class="table-striped"><tbody>
  <tr><td>23-Jun-26</td><td>Firm summary.</td><td>20-0223-2306</td><td>Firm</td><td>3 years</td></tr>
  <tr><td>23-Jun-26</td><td>Firm summary.</td><td>20-0223-2306</td><td>Individual</td><td>3 years</td></tr>
  <tr><td>11-Jun-26</td><td>Other case.</td><td>23-0178-1106</td><td>Firm</td><td>3 years</td></tr>
</tbody></table>
</body></html>
"""


def _build_spider(tmp_path, listing_html):
    """Build a list_only SavedHtmlMediaSpider over a saved listing, with a mocked discovery service."""
    source_config = {
        "source_name": "ADB",
        "dataset_name": "ADB_CASE_SUMMARIES",
        "url": URL,
        "acquisition": {"type": "crawler", "spider": "media"},
        "discovery": {
            "strategy": "list_only",
            "policy": "full_scan",
            "article": {
                "row_selector": "table.table-striped tbody tr",
                "identity_fields": {
                    "CaseNumber": {"source": "xpath", "selectors": ["./td[3]//text()"]},
                    "EntityType": {"source": "xpath", "selectors": ["./td[4]//text()"]},
                },
            },
        },
        "extraction": {
            "SourceURL": {"source": "response_url"},
            "date.originalValue": {"selector": "td:nth-child(1)", "output": "text"},
            "BodyText": {"selector": "td:nth-child(2)", "output": "text"},
            "CaseNumber": {"selector": "td:nth-child(3)", "output": "text"},
            "EntityType": {"selector": "td:nth-child(4)", "output": "text"},
            "SanctionPeriod": {"selector": "td:nth-child(5)", "output": "text"},
        },
        "storage": {"reuse_saved_detail_pages": False, "detail_directory": ""},
        "identity": {
            "record_key": {
                "fields": ["source_name", "dataset_name", "CaseNumber", "EntityType"],
            },
        },
    }

    listing_file = tmp_path / "adb_listing.html"
    listing_file.write_text(listing_html, encoding="utf-8")

    task = CrawlerTask(
        url=URL,
        source_name="ADB",
        list_name="ADB_CASE_SUMMARIES",
        source_config=source_config,
        source_file_path=str(listing_file),
        download_dir=str(tmp_path),
    )
    storage = CrawlerStorage(
        source_name="ADB",
        list_name="ADB_CASE_SUMMARIES",
        base_dir=str(tmp_path),
        detail_directory="",
    )

    discovery_service = MagicMock()
    discovery_service.build_record_key.side_effect = (
        lambda fields: f"ADB|ADB_CASE_SUMMARIES|{fields['CaseNumber']}|{fields['EntityType']}"
    )
    discovery_service.check_record_key.return_value = (False, False)

    spider = SavedHtmlMediaSpider(
        task=task,
        source_config=source_config,
        storage=storage,
        records=[],
        discovery_service=discovery_service,
    )

    return spider, discovery_service


async def _collect(spider):
    return [item async for item in spider.start()]


def test_each_row_becomes_one_record(tmp_path):
    spider, discovery_service = _build_spider(tmp_path, LISTING)

    results = asyncio.run(_collect(spider))

    assert [r["record_key"] for r in results] == [
        "ADB|ADB_CASE_SUMMARIES|20-0223-2306|Firm",
        "ADB|ADB_CASE_SUMMARIES|20-0223-2306|Individual",
        "ADB|ADB_CASE_SUMMARIES|23-0178-1106|Firm",
    ]
    assert results[1]["extracted"] == {
        "SourceURL": URL,
        "date.originalValue": "23-Jun-26",
        "BodyText": "Firm summary.",
        "CaseNumber": "20-0223-2306",
        "EntityType": "Individual",
        "SanctionPeriod": "3 years",
    }
    assert discovery_service.record_detail_selected.call_count == 3
    discovery_service.mark_source_end.assert_called_once()


def test_repeated_row_is_yielded_once(tmp_path):
    row = "<tr><td>23-Jun-26</td><td>Firm summary.</td><td>20-0223-2306</td><td>Firm</td><td>3 years</td></tr>"
    listing = LISTING.replace("<tbody>", "<tbody>" + row, 1)
    spider, _ = _build_spider(tmp_path, listing)

    results = asyncio.run(_collect(spider))

    assert len(results) == 3


def test_empty_table_marks_discovery_failure(tmp_path):
    spider, discovery_service = _build_spider(
        tmp_path, '<html><body><table class="table-striped"><tbody></tbody></table></body></html>'
    )

    results = asyncio.run(_collect(spider))

    assert results == []
    discovery_service.mark_discovery_failure.assert_called_once_with("EMPTY_FIRST_LISTING_PAGE")
    discovery_service.mark_source_end.assert_not_called()
