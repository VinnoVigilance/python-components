from unittest.mock import MagicMock
from pathlib import Path

import pytest
import yaml
from scrapy.http import HtmlResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider


def response(url, body):
    return HtmlResponse(url=url, body=body.encode(), encoding="utf-8")


@pytest.mark.parametrize("newest,expected", [(False, ["1", "2"]), (True, ["2", "1"])])
def test_month_and_decision_checks_follow_dates(newest, expected):
    service = MagicMock()
    service.stop_after_known = newest
    spider = MediaSpider(None, {"discovery": {
        "month_link_selector": "a.month", "article": {"link_selector": "a.case"}
    }}, None, [], service)
    root = response("https://example.com/", '''
        <a class="month" href="/docmonth/Feb/1996/1">Feb</a>
        <a class="month" href="/docmonth/Jan/1996/1">Jan</a>''')
    first = list(spider.parse_month_archive(root))[0]
    assert ("Feb" if newest else "Jan") in first.url
    month = response(first.url, '''
        <div id="left"><h3>''' + ("Feb" if newest else "Jan") + ''' 1996 | Decisions / Signed Resolutions</h3></div>
        <a class="case" href="/2">Case January 20, 1996</a>
        <a class="case" href="/1">Case January 1, 1996</a>''')
    checked = []
    spider._discover_detail = lambda url, node: (checked.append(url.rsplit("/", 1)[-1]), False)
    list(spider.parse_month_index(month, **first.cb_kwargs))
    assert checked == expected


def test_unparseable_decision_fails_index():
    service = MagicMock()
    spider = MediaSpider(None, {"discovery": {"article": {"link_selector": "a.case"}}}, None, [], service)
    url = "https://example.com/docmonth/Jan/1996/1"
    html = '<div id="left"><h3>Jan 1996 | Decisions / Signed Resolutions</h3></div><a class="case" href="/1">Missing date</a>'
    list(spider.parse_month_index(response(url, html), [url], 0, False))
    args, kwargs = service.mark_discovery_failure.call_args
    assert args == ("INVALID_MONTH_INDEX",)
    assert kwargs["retry_item"]["kind"] == "month_index"
    assert kwargs["retry_item"]["index_file_url"] == url


def test_empty_month_is_complete():
    service = MagicMock()
    spider = MediaSpider(None, {"discovery": {"article": {"link_selector": "a.case"}}}, None, [], service)
    url = "https://example.com/docmonth/Jan/1996/1"
    html = '<div id="left"><h3>Jan 1996 | Decisions / Signed Resolutions</h3></div>'
    assert list(spider.parse_month_index(response(url, html), [url], 0, False)) == []
    service.mark_source_end.assert_called_once()


def test_retry_month_rediscovery_returns_marker_and_detail_requests():
    service = MagicMock()
    config = {"discovery": {"article": {"link_selector": "a.case"}},
              "identity": {"record_key": {"fields": ["SourceURL"]}}}
    records = []
    spider = MediaSpider(None, config, None, records, service)
    url = "https://example.com/docmonth/Jan/1996/1"
    html = '<div id="left"><h3>Jan 1996 | Decisions / Signed Resolutions</h3></div><a class="case" href="/decision/1">Case January 1, 1996</a>'
    item = {"kind": "month_index", "record_key": "discovery:month", "index_file_url": url}
    marker, request = list(spider.retry_month_index(response(url, html), item))
    assert marker == {"record_key": "discovery:month", "discovery_retry_complete": True}
    assert records == [marker]
    assert request.url == "https://example.com/decision/1"


def test_retry_month_invalid_page_remains_pending():
    spider = MediaSpider(None, {"discovery": {"article": {"link_selector": "a.case"}}}, None, [], MagicMock())
    url = "https://example.com/docmonth/Jan/1996/1"
    item = {"kind": "month_index", "record_key": "discovery:month", "index_file_url": url}
    result = list(spider.retry_month_index(response(url, "<h1>Error</h1>"), item))
    assert result[0]["failed"] is True
    assert result[0]["discovery_retry_item"] == item


def test_decision_date_ignores_earlier_publication_date():
    with (Path(__file__).resolve().parents[4] / "config" / "mediaSources.yaml").open() as source:
        config = yaml.safe_load(source)["sources"]["SUPREME_COURT_PH_DECISIONS_AND_SIGNED_RESOLUTIONS"]
    spider = MediaSpider(None, config, None, [], MagicMock())
    page = response("https://example.com/decision/1", '''
        <div id="left"><div class="single_content">
          <p>Official Gazette, October 14, 1996</p>
          <h2>FIRST DIVISION</h2><h2>[ A.M. No. 123, January 31, 1996 ]</h2>
        </div></div>''')
    assert spider._extract_field(page, config["extraction"]["date.originalValue"]) == "January 31, 1996"
