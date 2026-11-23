from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scrapy.http import HtmlResponse, Response

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from ingestion.crawler.storage import CrawlerStorage
from services.adverseMediaPipeline.mediaDiscoveryService import resolve_discovery_mode


pytestmark = pytest.mark.unit
DATASET = "COURT_OF_APPEALS_PH_DECISIONS_AND_RESOLUTIONS"


@pytest.fixture
def source():
    path = Path(__file__).resolve().parents[4] / "config" / "mediaSources.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["sources"][DATASET]


def test_enabled_daily_source_uses_native_pdf_text_with_document_ocr_fallback(source):
    assert source["enabled"] is True
    assert source["active"] is True
    assert source["schedule"] == "daily"
    assert source["acquisition"]["spider"] == "media"
    assert source["parser"] == {
        "type": "pdf", "mode": "text", "require_text": True, "ocr_fallback": {}
    }
    assert source["post_normalization"]["body_input_format"] == "plain_text"


def test_initial_covers_three_bounded_stations_and_recent_incremental_only_recent(source):
    original = deepcopy(source)
    initial = resolve_discovery_mode(source, "INITIAL")
    incremental = resolve_discovery_mode(source, "INCREMENTAL")
    historical = initial["discovery"]["form_search"][:3]

    assert len(initial["discovery"]["form_search"]) == 4
    assert [recipe["parameters"]["station"]["values"] for recipe in historical] == [
        ["mnl"], ["ceb"], ["cdo"]
    ]
    assert [recipe["parameters"]["case_number"]["range"] for recipe in historical] == [
        {"start": 1, "end": 300000},
        {"start": 1, "end": 40000},
        {"start": 1, "end": 30000},
    ]
    assert all(recipe["parameters"]["case_number"]["format"] == "05d" for recipe in historical)
    recent = incremental["discovery"]["form_search"]
    assert initial["discovery"]["form_search"][-1] == recent
    assert "parameters" not in recent
    assert recent["start_url"] == source["source_page_url"]
    assert source == original


def test_actual_listing_rules_preserve_metadata_and_deduplicate_pdf_across_routes(source, tmp_path):
    initial = resolve_discovery_mode(source, "INITIAL")
    historical, *_, recent = initial["discovery"]["form_search"]
    storage = CrawlerStorage(source["source_name"], DATASET, str(tmp_path), None)
    spider = MediaSpider(None, initial, storage, [], None)
    spider.persist_pending_work = False

    recent_page = HtmlResponse(
        recent["start_url"], encoding="utf-8", body=b"""
        <table id="tbl-table"><tbody><tr>
          <td><span>CA-G.R. CR No. 12345</span> (November 20, 2026)</td>
          <td><span class="station-value">MANILA</span></td>
          <td><p>People v. Example</p></td>
        </tr></tbody></table>""",
    )
    recent_stage = recent["stages"][0]
    recent_row = recent_page.xpath(recent_stage["row_selector"])[0]
    recent_fields = spider._extract_record(recent_row, fields=recent_stage["fields"])
    assert recent_fields == {
        "Station": "mnl", "CaseNumber": "CA-G.R. CR No. 12345",
        "Title": "People v. Example", "date.originalValue": "November 20, 2026",
    }

    case_page = HtmlResponse(
        historical["start_url"], encoding="utf-8", body=b"""
        <table id="tbl-table"><tbody><tr>
          <td>CA-G.R. CR No. 12345</td><td></td>
          <td><p>People v. Example</p></td><td>Decided</td>
        </tr></tbody></table>""",
    )
    stage = historical["stages"][0]
    case_row = case_page.xpath(stage["row_selector"])[0]
    historical_fields = {"Station": "mnl", **spider._extract_record(case_row, fields=stage["fields"])}
    document_page = HtmlResponse(
        historical["start_url"], encoding="utf-8", body=b"""
        <table id="tbl-table"><tbody><tr><td>Decision November 20, 2026</td></tr></tbody></table>""",
    )
    stage = historical["stages"][1]
    document_row = document_page.xpath(stage["row_selector"])[0]
    historical_fields.update(spider._extract_record(document_row, fields=stage["fields"]))
    pdf = Response("https://services.ca.judiciary.gov.ph/document", body=b"%PDF-1.4\nfixture")

    spider._save_form_publication(pdf, {"recipe": historical}, historical_fields)
    spider._save_form_publication(pdf, {"recipe": recent}, recent_fields)

    assert len(spider.records) == 1
    record = spider.records[0]
    assert Path(record["detail_file_path"]).read_bytes() == pdf.body
    assert record["extracted"]["CaseStatus"] == "Decided"
    assert record["extracted"]["SourceRecordId"].startswith(
        "mnl|CA-G.R. CR No. 12345|November 20, 2026|"
    )
    assert len(list(storage.detail_path.glob("*.pdf"))) == 1
