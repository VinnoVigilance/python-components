"""Config-driven crawler that aggregates HTML watchlist records into JSONL."""

from datetime import datetime, timezone
from pathlib import Path

import scrapy

from ingestion.crawler.configLoader import PROJECT_ROOT
from ingestion.crawler.items import WatchlistRecordItem
from parsing.htmlParser import HtmlParser, get_nested_value


class GenericWatchlistSpider(scrapy.Spider):
    name = "generic_watchlist"
    custom_settings = {
        "ITEM_PIPELINES": {
            "ingestion.crawler.pipelines.WatchlistJsonlPipeline": 500,
        }
    }

    def __init__(self, source_config=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(source_config, dict):
            raise ValueError("source_config must be supplied as a dictionary")
        self.source_config = source_config
        self.project_root = PROJECT_ROOT
        self.engine = source_config.get("acquisition", {}).get("engine", "scrapy")
        self.run_started_at = datetime.now(timezone.utc)
        self.run_id = self.run_started_at.strftime("%Y%m%dT%H%M%S%fZ")
        self.html_parser = HtmlParser()

    async def start(self):
        yield self._page_request(self.source_config["discovery"]["url"], 1)

    def parse(self, response):
        records = self.html_parser.parse_content(
            response.text,
            self.source_config,
            parser_mode="records",
            base_url=response.url,
        )
        external_id_path = self.source_config["external_id_path"]
        for record in records:
            external_id = get_nested_value(record, external_id_path)
            if external_id is None and "." not in external_id_path:
                external_id = record.get(external_id_path)
            if external_id in (None, ""):
                self.logger.warning("Skipped record without external ID")
                continue
            yield WatchlistRecordItem(external_id=str(external_id), payload=record)

        next_rule = self.source_config["discovery"].get("next_page_selector")
        if not next_rule:
            return
        values = self.html_parser._select(response.selector, next_rule).getall()
        if values:
            next_page = int(response.meta.get("page_number", 1)) + 1
            yield self._page_request(response.urljoin(values[0]), next_page)

    def _page_request(self, url, page_number):
        meta = {
            "page_number": page_number,
            "save_raw_response": True,
            "storage_scope": "page",
        }
        if self.engine == "playwright":
            meta["playwright"] = True
        return scrapy.Request(url=url, callback=self.parse, meta=meta)
