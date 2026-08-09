"""Discover adverse-media articles and yield their downloaded HTML."""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import scrapy
from scrapy import Selector

from ingestion.crawler.articleExtractor import AdverseMediaArticleExtractor
from ingestion.crawler.items import AdverseMediaArticleItem


class GenericArticleSpider(scrapy.Spider):
    name = "generic_article"
    custom_settings = {
        "ITEM_PIPELINES": {
            "ingestion.crawler.pipelines.AdverseMediaHtmlStoragePipeline": 300,
        }
    }

    def __init__(self, source_config=None, result_files=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(source_config, dict):
            raise ValueError("source_config must be supplied as a dictionary")

        self.source_config = source_config
        self.acquisition = source_config["acquisition"]
        self.engine = self.acquisition["engine"]
        self.run_started_at = datetime.now(timezone.utc)
        self.run_id = self.run_started_at.strftime("%Y%m%dT%H%M%S%fZ")
        self.article_counter = 0
        self.article_extractor = AdverseMediaArticleExtractor()
        self.saved_files: list[dict[str, object]] = (
            result_files if isinstance(result_files, list) else []
        )

    async def start(self):
        start_page = int(self.acquisition.get("pagination", {}).get("start_page", 1))
        start_url = self._page_url(start_page)
        yield self._request(start_url, self.parse_discovery, {"page_number": start_page})

    def parse_discovery(self, response):
        for prefix, uri in self.acquisition.get("namespaces", {}).items():
            response.selector.register_namespace(prefix, uri)

        items = self._select(response.selector, self.acquisition["item_selector"])
        if not items:
            return

        for item in items:
            discovery_record = self._extract_discovery_record(item)
            article_url = discovery_record.get("article_url")
            source_record_id = discovery_record.get("source_record_id")
            if not article_url:
                self.logger.warning("Skipped a discovery item without an article URL")
                continue
            if source_record_id in (None, ""):
                self.logger.warning("Skipped a discovery item without source_record_id")
                continue

            self.article_counter += 1
            yield self._request(
                response.urljoin(str(article_url)),
                self.parse_article,
                {
                    "article_number": self.article_counter,
                    "discovery_url": response.url,
                    "discovery_record": discovery_record,
                    "source_record_id": str(source_record_id),
                },
            )

        next_request = self._next_discovery_request(response)
        if next_request is not None:
            yield next_request

    def parse_article(self, response):
        article_values = self.article_extractor.extract(
            response.body,
            self.source_config["mapping"],
            base_url=response.url,
        )
        yield AdverseMediaArticleItem(
            article_number=int(response.meta["article_number"]),
            source_record_id=response.meta["source_record_id"],
            article_url=response.url,
            discovery_url=response.meta["discovery_url"],
            discovery=response.meta["discovery_record"],
            article=article_values,
            html=response.body,
        )

    def _extract_discovery_record(self, item):
        record = {}
        for field_name, rule in self.source_config["mapping"].items():
            if rule.get("from") != "discovery":
                continue

            values = [
                value.strip()
                for value in self._select(item, rule).getall()
                if value and value.strip()
            ]
            if rule.get("clean_html"):
                values = [self._plain_text(value) for value in values]
                values = [value for value in values if value]

            if rule.get("join_with"):
                value = rule["join_with"].join(values)
            elif rule.get("multiple"):
                value = values
            else:
                value = values[0] if values else None

            record[field_name] = value
        return record

    def _next_discovery_request(self, response):
        pagination = self.acquisition.get("pagination", {})
        current_page = int(response.meta.get("page_number", 1))
        next_page = current_page + 1

        max_pages = pagination.get("max_pages")
        if max_pages is not None and next_page > int(max_pages):
            return None

        parameter = pagination.get("parameter")
        if parameter:
            return self._request(
                self._page_url(next_page),
                self.parse_discovery,
                {"page_number": next_page},
            )

        next_page_selector = pagination.get("next_page_selector")
        if next_page_selector:
            next_url = self._first(response.selector, next_page_selector)
            if next_url:
                return self._request(
                    response.urljoin(next_url),
                    self.parse_discovery,
                    {"page_number": next_page},
                )

        return None

    def _request(self, url, callback, meta):
        request_meta = dict(meta)
        if self.engine == "playwright":
            request_meta["playwright"] = True
            request_meta["playwright_page_goto_kwargs"] = {
                "wait_until": "networkidle"
            }
        return scrapy.Request(url=url, callback=callback, meta=request_meta)

    def _page_url(self, page_number):
        start_url = self.acquisition["start_url"]
        parameter = self.acquisition.get("pagination", {}).get("parameter")
        if not parameter:
            return start_url

        parsed = urlparse(start_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query[parameter] = [str(page_number)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    @staticmethod
    def _select(selector, rule):
        if rule.get("css"):
            return selector.css(rule["css"])
        return selector.xpath(rule["xpath"])

    @classmethod
    def _first(cls, selector, rule):
        value = cls._select(selector, rule).get()
        return value.strip() if value else None

    @staticmethod
    def _plain_text(html):
        text = Selector(text=html).xpath("string(.)").get() if html else None
        return " ".join(text.split()) if text else None
