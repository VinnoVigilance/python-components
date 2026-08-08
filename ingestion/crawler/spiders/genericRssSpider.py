"""Config-driven spider for standard, paginated RSS 2.0 feeds."""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import scrapy

from ingestion.crawler.configLoader import PROJECT_ROOT, load_source_config
from ingestion.crawler.items import ArticleItem


class GenericRssSpider(scrapy.Spider):
    """Extract common article fields from a standard RSS response."""

    name = "generic_rss"

    def __init__(
        self,
        source=None,
        mode="initial",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        if mode not in {"initial", "incremental"}:
            raise ValueError("mode must be 'initial' or 'incremental'")

        self.project_root = PROJECT_ROOT
        self.source_config = load_source_config(source)
        self.run_mode = mode
        self.run_started_at = datetime.now(timezone.utc)
        self.run_id = self.run_started_at.strftime("%Y%m%dT%H%M%S%fZ")

        acquisition = self.source_config["acquisition"]
        self.feed_url = acquisition["feed_url"]
        self.start_urls = [self.feed_url]

        self.known_article_urls = self._load_known_article_urls()

    async def start(self):
        start_page = int(self.source_config["pagination"]["start_page"])
        yield scrapy.Request(
            url=self._page_url(start_page),
            callback=self.parse,
            meta=self._request_meta(start_page),
        )

    def parse(self, response, **kwargs):
        if response.status in self.source_config["pagination"].get(
            "stop_http_statuses",
            [],
        ):
            return

        for prefix, uri in self.source_config["rss"].get(
            "namespaces",
            {},
        ).items():
            response.selector.register_namespace(prefix, uri)

        rss_items = response.xpath(
            self.source_config["rss"]["item_xpath"]
        )

        if not rss_items:
            return

        page_contains_known_article = False

        for rss_item in rss_items:
            article_url = self._mapped_text(rss_item, "article_url")
            summary_html = self._mapped_text(rss_item, "summary")
            content_html = self._text(
                rss_item,
                self._mapping_xpath("article_text"),
            )

            if article_url in self.known_article_urls:
                page_contains_known_article = True

            yield ArticleItem(
                source_record_id=self._mapped_text(
                    rss_item,
                    "source_record_id",
                ),
                source_name=self.source_config["source"]["name"],
                source_url=self.source_config["source"]["source_url"],
                article_url=article_url,
                title=self._mapped_text(rss_item, "title"),
                subtitle=None,
                summary=self._plain_text(summary_html),
                published_date=self._mapped_text(
                    rss_item,
                    "published_date",
                ),
                authors=self._authors(rss_item),
                article_text=self._plain_text(content_html),
                language=self._language(response),
                categories=self._mapped_all_text(rss_item, "categories"),
                tags=[],
                image_urls=self._image_urls(content_html, article_url),
                attachments=self._attachments(rss_item),
            )

        if (
            self.run_mode == "incremental"
            and page_contains_known_article
            and self.source_config["pagination"].get(
                "stop_when_known_article",
                True,
            )
        ):
            return

        current_page = int(response.meta.get("page", 1))
        max_pages = self.source_config["pagination"].get("max_pages")
        if max_pages is not None and current_page >= int(max_pages):
            return

        next_page = current_page + 1
        yield scrapy.Request(
            url=self._page_url(next_page),
            callback=self.parse,
            meta=self._request_meta(next_page),
        )

    @staticmethod
    def _text(selector, xpath):
        value = selector.xpath(xpath).get()
        return value.strip() if value else None

    @staticmethod
    def _all_text(selector, xpath):
        values = selector.xpath(xpath).getall()
        return [value.strip() for value in values if value.strip()]

    def _mapping_xpath(self, field_name):
        source_field = self.source_config["mapping"][field_name]
        return f"{source_field}/text()"

    def _mapped_text(self, rss_item, field_name):
        return self._text(rss_item, self._mapping_xpath(field_name))

    def _mapped_all_text(self, rss_item, field_name):
        return self._all_text(rss_item, self._mapping_xpath(field_name))

    def _authors(self, rss_item):
        author = self._mapped_text(rss_item, "authors")
        if not author:
            author = self._text(rss_item, "author/text()")
        return [author] if author else []

    @staticmethod
    def _plain_text(content_html):
        if not content_html:
            return None

        text = scrapy.Selector(text=content_html).xpath("string(.)").get()
        if not text:
            return None

        return " ".join(text.split())

    @staticmethod
    def _language(response):
        value = response.xpath("//channel/language/text()").get()
        return value.strip() if value else None

    @staticmethod
    def _image_urls(content_html, article_url):
        if not content_html:
            return []

        image_sources = scrapy.Selector(text=content_html).xpath(
            "//img/@src"
        ).getall()

        base_url = article_url or ""
        return list(
            dict.fromkeys(
                urljoin(base_url, source.strip())
                for source in image_sources
                if source.strip()
            )
        )

    @staticmethod
    def _attachments(rss_item):
        urls = rss_item.xpath("enclosure/@url").getall()
        return [url.strip() for url in urls if url.strip()]

    def _page_url(self, page_number):
        pagination = self.source_config["pagination"]
        parsed_url = urlparse(self.feed_url)
        query = parse_qs(parsed_url.query, keep_blank_values=True)
        query[pagination["parameter"]] = [str(page_number)]

        return urlunparse(
            parsed_url._replace(query=urlencode(query, doseq=True))
        )

    def _request_meta(self, page_number):
        return {
            "page": page_number,
            "save_raw_response": True,
            "handle_httpstatus_list": self.source_config["pagination"].get(
                "stop_http_statuses",
                [],
            ),
        }

    def _load_known_article_urls(self):
        if self.run_mode != "incremental":
            return set()

        output_path = Path(self.source_config["output"]["jsonl_path"])
        if not output_path.is_absolute():
            output_path = self.project_root / output_path

        if not output_path.is_file():
            return set()

        known_urls = set()
        with output_path.open(encoding="utf-8") as output_file:
            for line_number, line in enumerate(output_file, start=1):
                if not line.strip():
                    continue

                try:
                    article = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"Invalid JSONL at {output_path}:{line_number}"
                    ) from error

                article_url = article.get("article_url")
                if article_url:
                    known_urls.add(article_url)

        return known_urls
