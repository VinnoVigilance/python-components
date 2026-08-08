"""Config-driven article spider supporting RSS and HTML-list discovery."""

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import scrapy
from scrapy import Selector

from ingestion.crawler.configLoader import PROJECT_ROOT, load_source_config
from ingestion.crawler.items import ArticleItem
from parsing.htmlParser import HtmlParser, deep_merge_non_empty, get_nested_value, set_nested_value


class GenericArticleSpider(scrapy.Spider):
    name = "generic_article"
    custom_settings = {
        "ITEM_PIPELINES": {
            "ingestion.crawler.pipelines.ArticleValidationPipeline": 300,
            "ingestion.crawler.pipelines.ArticleJsonlPipeline": 500,
        }
    }

    def __init__(self, source=None, run_type="incremental", *args, **kwargs):
        super().__init__(*args, **kwargs)
        if run_type not in {"initial", "incremental"}:
            raise ValueError("run_type must be initial or incremental")
        self.project_root = PROJECT_ROOT
        self.source_config = load_source_config(source)
        self.run_type = run_type
        self.engine = self.source_config["acquisition"]["engine"]
        self.run_started_at = datetime.now(timezone.utc)
        self.run_id = self.run_started_at.strftime("%Y%m%dT%H%M%S%fZ")
        self.html_parser = HtmlParser()
        self.known_keys = self._load_known_keys()

    async def start(self):
        discovery = self.source_config["discovery"]
        callback = self.parse_rss if discovery["type"] == "rss" else self.parse_listing
        page = int(self.source_config.get("pagination", {}).get("start_page", 1))
        yield self._request(self._page_url(page), callback, {"page_number": page})

    def parse_rss(self, response):
        discovery = self.source_config["discovery"]
        for prefix, uri in discovery.get("namespaces", {}).items():
            response.selector.register_namespace(prefix, uri)
        nodes = response.xpath(discovery["item_xpath"])
        if not nodes:
            return

        page_keys = []
        for node in nodes:
            record = self._extract_discovery_record(node)
            set_nested_value(record, "urls.discovery", response.url)
            article_url = get_nested_value(record, "urls.article")
            if not article_url:
                continue
            record_key = self._record_key(record)
            dedup_key = self._dedup_key(record)
            page_keys.append(dedup_key)
            if self.run_type == "incremental" and dedup_key in self.known_keys:
                continue
            yield self._request(article_url, self.parse_article, {
                "discovery_record": record,
                "discovery_url": response.url,
                "record_key": record_key,
                "save_raw_response": True,
                "storage_scope": "record",
            })

        if self._should_continue(page_keys):
            next_page = int(response.meta.get("page_number", 1)) + 1
            yield self._request(self._page_url(next_page), self.parse_rss, {"page_number": next_page})

    def parse_listing(self, response):
        discovery = self.source_config["discovery"]
        links = self.html_parser._select(response.selector, discovery["article_link_selector"]).getall()
        for link in links:
            article_url = response.urljoin(link)
            record = deepcopy(self.source_config["defaults"])
            set_nested_value(record, "urls.discovery", response.url)
            set_nested_value(record, "urls.article", article_url)
            yield self._request(article_url, self.parse_article, {
                "discovery_record": record,
                "discovery_url": response.url,
                "record_key": self._record_key(record),
                "save_raw_response": True,
                "storage_scope": "record",
            })

        next_rule = discovery.get("next_page_selector")
        if next_rule:
            next_values = self.html_parser._select(response.selector, next_rule).getall()
            if next_values:
                next_page_number = int(response.meta.get("page_number", 1)) + 1
                yield self._request(response.urljoin(next_values[0]), self.parse_listing, {"page_number": next_page_number})

    def parse_article(self, response):
        detail = self.html_parser.parse_content(
            response.text, self.source_config, "article", response.url
        )
        record = deep_merge_non_empty(response.meta["discovery_record"], detail)
        set_nested_value(record, "urls.discovery", response.meta["discovery_url"])
        set_nested_value(record, "urls.article", response.url)
        yield ArticleItem(
            record=record,
            record_key=response.meta["record_key"],
            raw_file_path=response.meta.get("raw_file_path"),
        )

    def _extract_discovery_record(self, node):
        record = deepcopy(self.source_config["defaults"])
        for target_path, rule in self.source_config["discovery"].get("mapping", {}).items():
            values = [value.strip() for value in node.xpath(rule["xpath"]).getall() if value.strip()]
            if rule.get("clean_html"):
                values = [self._plain_text(value) for value in values]
                values = [value for value in values if value]
            value = values if rule.get("multiple", False) else (values[0] if values else None)
            if value not in (None, "", [], {}):
                set_nested_value(record, target_path, value)
        return record

    def _request(self, url, callback, meta):
        request_meta = dict(meta)
        request_meta["handle_httpstatus_list"] = self.source_config.get(
            "pagination", {}
        ).get("stop_http_statuses", [])
        if self.engine == "playwright":
            request_meta["playwright"] = True
        return scrapy.Request(
            url=url,
            callback=callback,
            meta=request_meta,
        )

    def _page_url(self, page_number):
        discovery_url = self.source_config["discovery"]["url"]
        pagination = self.source_config.get("pagination", {})
        if pagination.get("type") != "query_parameter":
            return discovery_url
        parsed = urlparse(discovery_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query[pagination["parameter"]] = [str(page_number)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    def _should_continue(self, page_keys):
        if not page_keys:
            return False
        if self.run_type != "incremental":
            return True
        stop = self.source_config.get("pagination", {}).get("stop_when_all_articles_known", True)
        return not (stop and all(key in self.known_keys for key in page_keys))

    def _record_key(self, record):
        article_url = str(get_nested_value(record, "urls.article", ""))
        last_part = urlparse(article_url).path.rstrip("/").split("/")[-1]
        if last_part.isdigit():
            return last_part
        raw = str(get_nested_value(record, "source.record_id") or article_url)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def _dedup_key(self, record):
        for path in self.source_config.get("deduplication", {}).get("keys", []):
            value = get_nested_value(record, path)
            if value:
                return f"{path}:{value}"
        return f"record:{self._record_key(record)}"

    def _load_known_keys(self):
        if self.run_type != "incremental":
            return set()
        output_path = Path(self.source_config["output"]["path"])
        if not output_path.is_absolute():
            output_path = self.project_root / output_path
        if not output_path.is_file():
            return set()
        keys = set()
        with output_path.open(encoding="utf-8") as output_file:
            for line_number, line in enumerate(output_file, start=1):
                if not line.strip():
                    continue
                try:
                    keys.add(self._dedup_key(json.loads(line)))
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSONL at {output_path}:{line_number}") from error
        return keys

    @staticmethod
    def _plain_text(html):
        text = Selector(text=html).xpath("string(.)").get() if html else None
        return " ".join(text.split()) if text else None
