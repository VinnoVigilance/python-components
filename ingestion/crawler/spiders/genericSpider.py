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
        self.current_url = response.url
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

            record_id = self._extract_record_id(detail_url, list_data)

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

        for field_name, field_config in self.config.get("list_fields", {}).items():
            attach_type = field_config.get("as_attachment")
            if attach_type:
                value = list_data.pop(field_name, None)
                if value:
                    attachments.append({"type": attach_type, "url": response.urljoin(value)})

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

        if attribute == "text":
            href = row.css(f"{selector}::text").get()
        else:
            href = row.css(f"{selector}::attr({attribute})").get()

        return response.urljoin(href.strip()) if href else None

    def _extract_fields(
        self,
        node,
        fields_config: dict[str, Any],
    ):
        extracted_fields = {}

        for field_name, field_config in fields_config.items():
            nested_fields = field_config.get("fields")

            if isinstance(nested_fields, dict) and nested_fields:
                extracted_fields[field_name] = (
                    self._extract_object_list(
                        node=node,
                        field_name=field_name,
                        field_config=field_config,
                    )
                )
            else:
                extracted_fields[field_name] = self._extract_value(
                    node=node,
                    field_config=field_config,
                )

        return extracted_fields

    def _extract_object_list(
        self,
        node,
        field_name: str,
        field_config: dict[str, Any],
    ):
        item_selector = field_config.get("selector")
        selector_type = str(
            field_config.get("selector_type", "css")
        ).strip().lower()

        item_fields = field_config.get("fields")

        if not item_selector:
            raise ValueError(
                f"selector is required for nested field: {field_name}"
            )

        if selector_type == "xpath":
            item_nodes = node.xpath(item_selector)

        elif selector_type == "css":
            item_nodes = node.css(item_selector)

        else:
            raise ValueError(
                f"Unsupported selector_type for nested field "
                f"'{field_name}': {selector_type}"
            )

        extracted_items = []

        for item_node in item_nodes:
            item = self._extract_fields(
                node=item_node,
                fields_config=item_fields,
            )

            has_meaningful_value = any(
                value not in (None, "", [], {})
                for value in item.values()
            )

            if has_meaningful_value:
                extracted_items.append(item)

        return extracted_items

    def _extract_value(self, node, field_config):
        selector = field_config.get("selector")
        selector_type = field_config.get("selector_type", "css")
        multiple = field_config.get("multiple", False)
        value_type = field_config.get("value", "text")
        join = field_config.get("join")

        if selector_type == "xpath":
            values = node.xpath(selector).getall()

            if "@href" in selector or "@src" in selector:
                values = [
                    urljoin(self.current_url, value) if value else value
                    for value in values
                ]

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

        if join is not None:
            return join.join(cleaned_values)

        if multiple:
            return cleaned_values

        return cleaned_values[0] if cleaned_values else None

    def _extract_record_id(self, detail_url, list_data):
        record_config = self.config.get("record_id", {})
        strategy = record_config.get("strategy")

        if strategy == "field":
            value = list_data.get(record_config.get("source"))
            return str(value).strip() if value else None

        if strategy == "url_regex":
            pattern = record_config.get("pattern")

            if not pattern:
                return None

            match = re.search(pattern, detail_url)

            return match.group(1) if match else None

        raise ValueError(f"Unsupported record_id strategy: {strategy}")

    def _extract_attachments(self, response, detail_url):
        attachments = []

        detail_type = "DETAIL_PAGE"
        selector_configs = []

        for config in self.config.get("attachments", []):
            if config.get("role") == "detail_page" or config.get("type") == "DETAIL_PAGE":
                detail_type = config.get("type", "DETAIL_PAGE")
            else:
                selector_configs.append(config)

        attachments.append(
            {
                "type": detail_type,
                "url": detail_url,
            }
        )

        for config in selector_configs:
            selector = config.get("selector")

            if not selector:
                continue

            attribute = config.get(
                "attribute",
                "src",
            )

            if config.get("multiple"):
                seen = set()

                for node in response.css(selector):
                    url = node.attrib.get(attribute)

                    if not url or url in seen:
                        continue

                    seen.add(url)

                    attachment = {
                        "type": config["type"],
                        "url": response.urljoin(url),
                    }

                    metadata = self._extract_attachment_metadata(
                        node, config.get("metadata")
                    )

                    if metadata:
                        attachment["metadata"] = metadata

                    attachments.append(attachment)

                continue

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

    def _extract_attachment_metadata(self, node, metadata_config):
        """Per-attachment metadata from a node's attributes; a rule's optional
        `pattern` keeps the value only when it matches (e.g. a photo date)."""
        if not metadata_config:
            return None

        metadata = {}

        for key, spec in metadata_config.items():
            attribute = spec.get("attribute")
            value = self._clean_text(node.attrib.get(attribute)) if attribute else None

            pattern = spec.get("pattern")

            if value and pattern:
                match = re.search(pattern, value)
                value = (
                    (match.group(1) if match.groups() else match.group(0))
                    if match
                    else None
                )

            if value:
                metadata[key] = value

        return metadata or None

    @staticmethod
    def _clean_text(value):
        return " ".join(value.split()).strip() if value is not None else None