from unittest.mock import MagicMock

import pytest
from scrapy.http import HtmlResponse

from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.spiders.savedHtmlMediaSpider import SavedHtmlMediaSpider
from ingestion.crawler.storage import CrawlerStorage
from utils.jsonlRecordBuffer import JsonlRecordBuffer


pytestmark = pytest.mark.unit
URL = "https://court.example/cases/"
PDF = "https://court.example/order.pdf"


def _spider(tmp_path, monkeypatch, *, fail=False):
    config = {
        "source_name": "COURT", "dataset_name": "ENTRIES",
        "discovery": {"strategy": "list_only", "article": {
            "row_selector": "table tbody tr", "fields": {
                "CaseNumber": {"selector": "td:first-child"},
                "PDFURL": {"selector": "a", "output": "attribute", "attribute": "href"},
            },
        }},
        "document_download": {
            "manifest": True, "failure_policy": "retain_metadata", "required": False,
            "transport": "browser", "type": "pdf", "retry": 2, "url_fields": ["PDFURL"],
        },
    }
    discovery = MagicMock()
    discovery.build_record_key.side_effect = lambda fields: fields["CaseNumber"]
    discovery.check_record_key.return_value = (False, False)
    records = JsonlRecordBuffer(tmp_path / "records.jsonl")
    task = CrawlerTask(url=URL, source_name="COURT", list_name="ENTRIES", source_config=config)
    spider = SavedHtmlMediaSpider(
        task=task, source_config=config,
        storage=CrawlerStorage(source_name="COURT", list_name="ENTRIES", base_dir=str(tmp_path)),
        records=records, discovery_service=discovery,
    )
    fetcher = MagicMock()
    closed = []

    def fetch(items):
        try:
            yield items[0], None if fail else HtmlResponse(url=URL)
        finally:
            closed.append(True)

    fetcher.fetch.side_effect = fetch
    fetcher.download_binary.return_value = b"%PDF-original"
    monkeypatch.setattr(
        "ingestion.crawler.spiders.savedHtmlMediaSpider.BrowserDetailFetcher",
        MagicMock(return_value=fetcher),
    )
    return spider, fetcher, closed


def _response(*links):
    rows = "".join(f'<tr><td>C-{i}</td><td>{link}</td></tr>' for i, link in enumerate(links))
    return HtmlResponse(url=URL, body=f"<table><tbody>{rows}</tbody></table>", encoding="utf-8")


def test_listing_attachments_are_serialized_before_buffering_and_downloaded_once(tmp_path, monkeypatch):
    spider, fetcher, closed = _spider(tmp_path, monkeypatch)
    try:
        spider._collect_listing_rows(_response('<a href="/order.pdf">Order</a>',
                                              '<a href="/order.pdf">Order</a>', ""))
        saved = list(spider.records)
        assert len(saved) == 3
        assert all(record["retry_listing"] and record["attachment_manifest"] for record in saved)
        assert saved[0]["extracted"]["DocumentFiles"][0]["url"] == PDF
        assert saved[1]["extracted"]["DocumentFiles"] == saved[0]["extracted"]["DocumentFiles"]
        assert saved[2]["extracted"]["DocumentFiles"] == []
        assert saved[2]["extracted"]["DocumentErrors"] == []
        fetcher.download_binary.assert_called_once()
        fetcher.fetch.assert_called_once()
        assert closed == [True]
    finally:
        spider.records.close()


def test_rows_without_documents_never_open_a_browser(tmp_path, monkeypatch):
    spider, fetcher, _ = _spider(tmp_path, monkeypatch)
    try:
        spider._collect_listing_rows(_response(""))
        assert len(spider.records) == 1
        fetcher.fetch.assert_not_called()
    finally:
        spider.records.close()


@pytest.mark.parametrize("browser_failure", [True, False])
def test_attachment_failure_keeps_metadata_and_error_in_disk_buffer(tmp_path, monkeypatch, browser_failure):
    spider, fetcher, closed = _spider(tmp_path, monkeypatch, fail=browser_failure)
    fetcher.download_binary.side_effect = RuntimeError("download failed")
    try:
        spider._collect_listing_rows(_response('<a href="/order.pdf">Order</a>'))
        [record] = list(spider.records)
        assert record["extracted"]["CaseNumber"] == "C-0"
        assert record["extracted"]["DocumentErrors"]
        assert not record.get("failed")
        assert fetcher.download_binary.call_count == (0 if browser_failure else 2)
        assert closed == [True]
    finally:
        spider.records.close()


def test_invalid_pdf_bytes_are_retried_before_record_is_buffered(tmp_path, monkeypatch):
    spider, fetcher, _ = _spider(tmp_path, monkeypatch)
    fetcher.download_binary.side_effect = [b"<html>challenge</html>", b"%PDF-original"]
    try:
        spider._collect_listing_rows(_response('<a href="/order.pdf">Order</a>'))
        [record] = list(spider.records)
        assert fetcher.download_binary.call_count == 2
        assert record["extracted"]["DocumentErrors"] == []
        assert record["extracted"]["DocumentFiles"]
    finally:
        spider.records.close()
