import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, urljoin

import scrapy


class GenericSpider(scrapy.Spider):
    name = "generic_source_spider"

    def __init__(self, task, crawler_config, storage, records, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.task = task
        self.config = crawler_config
        self.storage = storage
        self.records = records

    async def start(self):
        yield scrapy.Request(url=self._build_start_url(), callback=self.parse, dont_filter=True)

    def parse(self, response):
        storage_config = self.config.get("storage", {})

        if storage_config.get("save_listing_page", False):
            self.storage.save_source_html(response.text)

        row_selector = self.config.get("discovery", {}).get("row_selector")

        if not row_selector:
            raise ValueError("row_selector is required")

        for row in response.css(row_selector):
            list_data = self._extract_fields(row, self.config.get("list_fields", {}))
            detail_url = self._extract_detail_url(row, response)

            if not detail_url:
                continue

            record_id = self._extract_record_id(detail_url)

            if not record_id:
                continue

            yield scrapy.Request(
                url=detail_url,
                callback=self.parse_detail,
                cb_kwargs={
                    "list_data": list_data,
                    "record_id": record_id,
                    "detail_url": detail_url,
                },
            )

    def parse_detail(self, response, list_data, record_id, detail_url):
        self.current_url = response.url
        detail_data = self._extract_fields(response, self.config.get("detail_fields", {}))
        detail_file_path = None

        if self.config.get("storage", {}).get("save_detail_pages", False):
            detail_file_path = self.storage.save_detail_html(
                record_id=record_id,
                content=response.text,
            )

        attachments = self._extract_attachments(
            response=response,
            detail_url=detail_url,
        )

        record = {
            "source_record_id": record_id,
            "list": list_data,
            "detail": detail_data,
            "attachments": attachments,
        }

        self.records.append(record)
        yield record

    def _build_start_url(self):
        pagination = self.config.get("pagination", {})

        if pagination.get("type", "none") != "query_params":
            return self.task.url

        params = pagination.get("params", {})

        if not params:
            return self.task.url

        url_parts = urlsplit(self.task.url)
        query = dict(parse_qsl(url_parts.query))
        query.update(params)

        return urlunsplit(
            (
                url_parts.scheme,
                url_parts.netloc,
                url_parts.path,
                urlencode(query),
                url_parts.fragment,
            )
        )

    def _extract_detail_url(self, row, response):
        discovery = self.config.get("discovery", {})
        selector = discovery.get("detail_link_selector")
        attribute = discovery.get("detail_link_attribute", "href")

        if not selector:
            return None

        href = row.css(f"{selector}::attr({attribute})").get()

        return response.urljoin(href) if href else None

    def _extract_fields(self, node, fields_config: dict[str, Any]):
        return {
            field_name: self._extract_value(node, field_config)
            for field_name, field_config in fields_config.items()
        }

    def _extract_value(self, node, field_config):
        selector = field_config.get("selector")
        selector_type = field_config.get("selector_type", "css")
        multiple = field_config.get("multiple", False)
        value_type = field_config.get("value", "text")

        if selector_type == "xpath":
            values = node.xpath(selector).getall()

        elif selector_type == "css":
            if value_type == "text":
                values = node.css(f"{selector}::text").getall()

            elif value_type == "attribute":
                attribute = field_config.get("attribute")

                value = node.css(selector).attrib.get(attribute)

                if value and attribute in ["src", "href"]:
                    value = urljoin(self.current_url, value)

                values = [value] if value else []

            else:
                values = node.css(selector).getall()

        else:
            raise ValueError(f"Unsupported selector_type: {selector_type}")

        cleaned_values = [self._clean_text(value) for value in values]
        cleaned_values = [value for value in cleaned_values if value]

        if multiple:
            return cleaned_values

        return cleaned_values[0] if cleaned_values else None

    def _extract_record_id(self, detail_url):
        record_config = self.config.get("record_id", {})

        if record_config.get("strategy") != "url_regex":
            raise ValueError("Only url_regex is currently supported")

        pattern = record_config.get("pattern")

        if not pattern:
            return None

        match = re.search(pattern, detail_url)

        return match.group(1) if match else None

    def _extract_attachments(self, response, detail_url):
        attachments = []

        attachments.append(
            {
                "type": "DETAIL_PAGE",
                "url": detail_url,
            }
        )

        for config in self.config.get("attachments", []):
            if config["type"] == "DETAIL_PAGE":
                continue

            selector = config.get("selector")

            if not selector:
                continue

            attribute = config.get(
                "attribute",
                "src",
            )

            url = response.css(
                selector
            ).attrib.get(attribute)

            if url:
                attachments.append(
                    {
                        "type": config["type"],
                        "url": response.urljoin(url),
                    }
                )

        return attachments

    @staticmethod
    def _clean_text(value):
        return " ".join(value.split()).strip() if value is not None else None