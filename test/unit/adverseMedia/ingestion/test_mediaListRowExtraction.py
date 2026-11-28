from pathlib import Path
from unittest.mock import MagicMock

import pytest
from scrapy.http import HtmlResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from ingestion.crawler.storage import CrawlerStorage
from services.adverseMediaPipeline.mediaIdentityService import MediaIdentityService


pytestmark = pytest.mark.unit
URL = "https://court.example/cases/"


def _table(rows, columns=("Case Number", "Next Hearing", "Pleadings")):
    headers = "".join(f"<th>{name}</th>" for name in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{row.get(name, '')}</td>" for name in columns) + "</tr>"
        for row in rows
    )
    return f"<table id='cases'><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>"


def _spider(tmp_path):
    fields = {}
    for name, header in (("CaseNumber", "Case Number"), ("NextHearing", "Next Hearing"),
                         ("Pleadings", "Pleadings")):
        fields[name] = {
            "selector_type": "xpath",
            "selector": f"./td[count(ancestor::table[1]/thead/tr/th[.='{header}']/preceding-sibling::th)+1]",
            "required": name == "CaseNumber",
        }
    fields["PDFURL"] = {
        "selector_type": "xpath",
        "selector": "./td//a[@href]",
        "output": "attribute",
        "attribute": "href",
        "max_matches": 1,
        "extraction": {"strategy": "regex", "pattern": r"^(.+\.pdf)(?:%E2%80%9D)?$"},
    }
    config = {
        "source_name": "COURT",
        "dataset_name": "CASES",
        "discovery": {
            "strategy": "list_only",
            "article": {
                "selector_type": "xpath",
                "row_selector": "//table[@id='cases']/tbody/tr",
                "preserve_table_headers": True,
                "fields": fields,
            },
        },
        "identity": {"record_key": {
            "hash": True,
            "fields": ["source_name", "dataset_name", "CaseNumber", "NextHearing", "Pleadings", "PDFURL"],
        }},
    }
    discovery = MagicMock()
    discovery.build_record_key.side_effect = lambda record: MediaIdentityService.generate_record_key(config, record)
    discovery.check_record_key.return_value = (False, False)
    storage = CrawlerStorage(source_name="COURT", list_name="CASES", base_dir=str(tmp_path), detail_directory="")
    spider = MediaSpider(task=None, source_config=config, storage=storage, records=[], discovery_service=discovery)
    return spider, discovery


def _parse(spider, html):
    return list(spider.parse_list_rows(HtmlResponse(url=URL, body=html, encoding="utf-8")))


@pytest.mark.parametrize("columns", [
    ("Case Number", "Next Hearing", "Pleadings"),
    ("Pleadings", "Case Number", "Next Hearing"),
])
def test_header_based_fields_and_cleaned_link_reach_identity_and_saved_record(tmp_path, columns):
    spider, discovery = _spider(tmp_path)
    rows = [{"Case Number": "C-1", "Next Hearing": "December 1", "Pleadings":
             '<a href="/documents/order.pdf%E2%80%9D">Order</a>'}]

    results = _parse(spider, _table(rows, columns))

    assert len(results) == 1
    record = results[0]["extracted"]
    assert record == {"CaseNumber": "C-1", "NextHearing": "December 1", "Pleadings": "Order",
                      "PDFURL": "https://court.example/documents/order.pdf", "SourceURL": URL}
    assert discovery.build_record_key.call_args.args[0] == record
    assert results[0]["record_key"] == MediaIdentityService.generate_record_key(spider.source_config, record)
    saved = HtmlResponse(url=URL, body=Path(results[0]["detail_file_path"]).read_bytes(), encoding="utf-8")
    assert saved.xpath("//thead/tr/th/text()").getall() == list(columns)
    assert len(saved.xpath("//tbody/tr")) == 1
    assert spider._extract_record(saved.xpath("//tbody/tr")[0],
                                  fields=spider.discovery_config["article"]["fields"], base_url=URL) == {
        name: value for name, value in record.items() if name != "SourceURL"
    }


def test_distinct_entries_share_case_number_and_duplicate_entry_is_skipped(tmp_path):
    spider, discovery = _spider(tmp_path)
    first = {"Case Number": "C-1", "Next Hearing": "December 1", "Pleadings": "Order"}
    rows = [first, first, {**first, "Next Hearing": "December 2"},
            {**first, "Pleadings": "Resolution"},
            {**first, "Pleadings": '<a href="/order.pdf">Order</a>'}]

    results = _parse(spider, _table(rows))

    assert len(results) == len({result["record_key"] for result in results}) == 4
    assert results[0]["extracted"]["PDFURL"] is None
    assert discovery.check_record_key.call_count == 4
    discovery.mark_source_end.assert_called_once()


@pytest.mark.parametrize("invalid", [
    {"Case Number": "", "Pleadings": "Order"},
    {"Case Number": "C-bad", "Pleadings": '<a href="/a.pdf">A</a><a href="/b.pdf">B</a>'},
])
def test_invalid_row_is_reported_without_losing_valid_sibling(tmp_path, invalid):
    spider, discovery = _spider(tmp_path)

    results = _parse(spider, _table([invalid, {"Case Number": "C-good", "Pleadings": "Order"}]))

    assert [result["extracted"]["CaseNumber"] for result in results] == ["C-good"]
    discovery.record_identity_failure.assert_called_once()
    assert discovery.check_record_key.call_count == 1


def test_preserving_headers_rejects_table_without_headers(tmp_path):
    spider, _ = _spider(tmp_path)
    spider.discovery_config["article"]["fields"] = {"CaseNumber": {"selector": "td"}}

    with pytest.raises(ValueError, match="no table headers"):
        _parse(spider, "<table id='cases'><tbody><tr><td>C-1</td></tr></tbody></table>")


@pytest.mark.parametrize("limit, multiple, expected", [
    (None, False, "https://court.example/a.pdf"),
    (None, True, ["https://court.example/a.pdf", "https://court.example/b.pdf"]),
    (2, True, ["https://court.example/a.pdf", "https://court.example/b.pdf"]),
])
def test_existing_link_extraction_remains_available_without_single_match_limit(tmp_path, limit, multiple, expected):
    spider, _ = _spider(tmp_path)
    response = HtmlResponse(url=URL, body='<a href="/a.pdf">A</a><a href="/b.pdf">B</a>', encoding="utf-8")
    field = {"selector": "a", "output": "attribute", "attribute": "href", "multiple": multiple}
    if limit is not None:
        field["max_matches"] = limit

    assert spider._extract_field(response, field) == expected
