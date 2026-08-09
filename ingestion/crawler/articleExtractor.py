"""Selector helpers used only by the adverse-media crawler."""

import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from scrapy import Selector


class AdverseMediaArticleExtractor:
    """Extract configured article fields directly inside crawler infrastructure."""

    def extract(
        self,
        html: str | bytes,
        mapping: dict[str, Any],
        base_url: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")

        selector = Selector(text=html)
        values: dict[str, Any] = {}
        for field_name, rule in mapping.items():
            if rule.get("from") != "article":
                continue
            values[field_name] = self._extract_value(selector, rule, base_url)
        return values

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
                values = [
                    urljoin(base_url or "", value.strip())
                    for value in raw_values
                    if value and value.strip()
                ]
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
                links.append(
                    {
                        "title": self.clean_text(node.xpath("string(.)").get()),
                        "url": urljoin(base_url or "", href),
                    }
                )
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
            attachments.append(
                {
                    "title": self.clean_text(node.xpath("string(.)").get()),
                    "url": absolute_url,
                    "file_name": file_name,
                    "mime_type": mime_type or "application/octet-stream",
                }
            )
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
