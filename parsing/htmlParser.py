"""Config-driven HTML parsing for articles and watchlist datasets."""

import json
import mimetypes
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from scrapy import Selector


def set_nested_value(record: dict[str, Any], path: str, value: Any) -> None:
    current = record
    parts = path.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def get_nested_value(record: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def deep_merge_non_empty(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict):
            result[key] = deep_merge_non_empty(result.get(key, {}), value)
        elif value not in (None, "", [], {}):
            result[key] = value
    return result


class HtmlParser:
    def parse(self, file_path, config):
        """Maintain the existing WatchlistPipeline parser contract."""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8", errors="replace")
        parser_config = config.get("parser", {})
        parser_mode = parser_config.get("mode") or config.get("parser_mode")
        if parser_mode in {"article", "records"}:
            return self.parse_content(
                content, config, parser_mode=parser_mode, base_url=config.get("url")
            )

        handlers = {
            "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": self.parse_atc_designated_terrorist_individuals,
            "ATC-DESIGNATED-TERRORIST-GROUPS": self.parse_atc_designated_terrorist_groups,
        }
        handler = handlers.get(config.get("list_name"))
        if handler is None:
            raise ValueError(f"No HTML handler found for source: {config.get('list_name')}")
        return handler(file_path)

    def parse_content(self, content, config, parser_mode, base_url=None):
        if parser_mode == "article":
            return self.parse_article(content, config, base_url)
        if parser_mode == "records":
            return self.parse_records(content, config, base_url)
        raise ValueError(f"Unsupported parser mode: {parser_mode}")

    def parse_article(self, content, config, base_url=None):
        selector = Selector(text=content)
        record = deepcopy(config["defaults"])
        for target_path, rule in config.get("article", {}).get("selectors", {}).items():
            value = self._extract_value(selector, rule, base_url)
            if value not in (None, "", [], {}):
                set_nested_value(record, target_path, value)
        return record

    def parse_records(self, content, config, base_url=None):
        selector = Selector(text=content)
        parser_config = config["parser"]
        record_nodes = self._select(selector, parser_config["record_selector"])
        records = []
        for node in record_nodes:
            record = {}
            for target_path, rule in parser_config["fields"].items():
                value = self._extract_value(node, rule, base_url)
                if value not in (None, "", [], {}):
                    set_nested_value(record, target_path, value)
            if record:
                records.append(record)
        return records

    def _extract_value(self, selector, rule, base_url=None):
        selected = self._select(selector, rule)
        output_type = rule.get("output", "text")
        multiple = bool(rule.get("multiple", False))
        if output_type == "link":
            values = self._extract_links(selected, base_url)
        elif output_type == "attachment":
            values = self._extract_attachments(selected, base_url)
        else:
            raw_values = selected.getall()
            if output_type == "url":
                values = [urljoin(base_url or "", value.strip()) for value in raw_values if value.strip()]
            elif output_type == "html":
                values = [value.strip() for value in raw_values if value.strip()]
            else:
                values = [self.clean_text(value) for value in raw_values]
                values = [value for value in values if value]
        values = self._deduplicate(values)
        if rule.get("join_with"):
            return rule["join_with"].join(str(value) for value in values)
        if multiple:
            return values
        return values[0] if values else None

    @staticmethod
    def _select(selector, rule):
        if isinstance(rule, str):
            return selector.css(rule)
        if rule.get("css"):
            return selector.css(rule["css"])
        if rule.get("xpath"):
            return selector.xpath(rule["xpath"])
        raise ValueError(f"Selector rule requires css or xpath: {rule}")

    def _extract_links(self, selected, base_url):
        links = []
        for node in selected:
            href = node.attrib.get("href")
            if href:
                links.append({
                    "title": self.clean_text(node.xpath("string(.)").get()),
                    "url": urljoin(base_url or "", href),
                })
        return links

    def _extract_attachments(self, selected, base_url):
        attachments = []
        allowed = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip"}
        for node in selected:
            href = node.attrib.get("href")
            if not href:
                continue
            absolute_url = urljoin(base_url or "", href)
            file_name = Path(urlparse(absolute_url).path).name
            if Path(file_name).suffix.lower() not in allowed:
                continue
            mime_type, _ = mimetypes.guess_type(file_name)
            attachments.append({
                "title": self.clean_text(node.xpath("string(.)").get()),
                "url": absolute_url,
                "file_name": file_name,
                "mime_type": mime_type or "application/octet-stream",
            })
        return attachments

    @staticmethod
    def _deduplicate(values):
        unique = []
        seen = set()
        for value in values:
            marker = json.dumps(value, sort_keys=True, ensure_ascii=False)
            if marker not in seen:
                seen.add(marker)
                unique.append(value)
        return unique

    @staticmethod
    def clean_text(value):
        if value is None:
            return None
        cleaned = " ".join(str(value).split())
        return cleaned or None

    def parse_atc_designated_terrorist_individuals(self, file_path):
        html = Path(file_path).read_text(encoding="utf-8", errors="replace")
        selector = Selector(text=html)
        records = []
        for row in selector.xpath('//table[@id="tablepress-33"]//tbody/tr'):
            name = self.clean_text(" ".join(row.xpath('.//td[contains(@class, "column-1")]//text()').getall()))
            if not name:
                continue
            records.append({
                "entity_type": "Individual",
                "name": name,
                "category": self.clean_text(" ".join(row.xpath('.//td[contains(@class, "column-2")]//text()').getall())),
                "atc_resolution_no": self.clean_text(" ".join(row.xpath('.//td[contains(@class, "column-3")]//text()').getall())),
                "date_issued": self.clean_text(" ".join(row.xpath('.//td[contains(@class, "column-4")]//text()').getall())),
                "detail_url": row.xpath('.//td[contains(@class, "column-1")]//a/@href').get() or "",
            })
        return records

    def parse_atc_designated_terrorist_groups(self, file_path):
        html = Path(file_path).read_text(encoding="utf-8", errors="replace")
        selector = Selector(text=html)
        records = []
        for row in selector.xpath('//table[@id="tablepress-31"]//tbody/tr'):
            name = self.clean_text(" ".join(row.xpath('.//td[contains(@class, "column-1")]//text()').getall()))
            if not name:
                continue
            records.append({
                "entity_type": "Entity",
                "name": name,
                "category": self.clean_text(" ".join(row.xpath('.//td[contains(@class, "column-2")]//text()').getall())),
                "atc_resolution_no": self.clean_text(" ".join(row.xpath('.//td[contains(@class, "column-3")]//text()').getall())),
                "date_issued": self.clean_text(" ".join(row.xpath('.//td[contains(@class, "column-4")]//text()').getall())),
            })
        return records
