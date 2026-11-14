"""Checks for the US Treasury press release extraction configuration."""

from pathlib import Path

import yaml
from scrapy.http import HtmlResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider


def test_treasury_extracts_press_release_fields():
    root = Path(__file__).resolve().parents[4]
    config = yaml.safe_load((root / "config/mediaSources.yaml").read_text(encoding="utf-8"))
    source = config["sources"]["US_TREASURY_PRESS_RELEASES"]
    response = HtmlResponse(
        url="https://home.treasury.gov/news/press-releases/example",
        body=b'''<div id="block-hamilton-page-title"><h2>Example release</h2></div>
        <div class="field--name-field-news-publication-date"><time>June 1, 2026</time></div>
        <div class="field--name-field-news-body"><p>Release text</p>
        <a href="/files/report.pdf">Report</a></div>''',
        encoding="utf-8",
    )
    spider = MediaSpider(task=None, source_config=source, storage=None, records=[], discovery_service=None)
    fields = {name: spider._extract_field(response, rule) for name, rule in source["extraction"].items()}

    assert spider._extract_source_record_id(response.url) == "example"
    assert fields["Title"] == "Example release"
    assert fields["date.originalValue"] == "June 1, 2026"
    assert "Release text" in fields["BodyText"]
    assert fields["PdfUrls"] == ["https://home.treasury.gov/files/report.pdf"]
