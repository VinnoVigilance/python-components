"""Scrapy pipelines used by the shared crawler infrastructure."""

import json
import re
from pathlib import Path

from scrapy.exceptions import DropItem

from ingestion.crawler.configLoader import PROJECT_ROOT
from ingestion.crawler.items import AdverseMediaArticleItem


class AdverseMediaHtmlStoragePipeline:
    """Save raw article HTML and expose its path to the application pipeline."""

    def process_item(self, item, spider):
        if not isinstance(item, AdverseMediaArticleItem):
            return item

        html = item.get("html")
        if not html:
            raise DropItem("Downloaded article HTML is empty")
        if isinstance(html, str):
            html = html.encode("utf-8")

        article_number = int(item["article_number"])
        source_record_id = str(item["source_record_id"]).strip()
        if not source_record_id:
            raise DropItem("source_record_id is required for article HTML storage")
        file_name = f"{_safe_name(source_record_id)}.html"
        run_directory = self._run_directory(spider)
        file_path = run_directory / file_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(html)

        relative_path = file_path.relative_to(PROJECT_ROOT)
        item["raw_file_path"] = str(relative_path)
        spider.saved_files.append(
            {
                "article_number": article_number,
                "source_record_id": source_record_id,
                "article_url": item["article_url"],
                "discovery_url": item["discovery_url"],
                "discovery": dict(item["discovery"]),
                "article": dict(item["article"]),
                "file_path": str(relative_path),
            }
        )

        # Release the response body as soon as it has been persisted.
        del item["html"]
        spider.crawler.stats.inc_value("article_download/count")
        return item

    @staticmethod
    def _run_directory(spider) -> Path:
        source = spider.source_config["source"]
        return (
            PROJECT_ROOT
            / "data"
            / "downloads"
            / _safe_name(source["id"])
            / _safe_name(source["section"])
            / f"year={spider.run_started_at:%Y}"
            / f"month={spider.run_started_at:%m}"
            / f"day={spider.run_started_at:%d}"
            / f"run={spider.run_id}"
        )


def _safe_name(value):
    safe_value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return safe_value or "unknown"


class WatchlistJsonlPipeline:
    """Existing aggregate JSONL writer for Watchlist crawler records."""

    def open_spider(self, spider):
        output_path = Path(spider.source_config["output"]["path"])
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_file = output_path.open("w", encoding="utf-8")

    def process_item(self, item, spider):
        if "payload" in item:
            self.output_file.write(
                json.dumps(dict(item["payload"]), ensure_ascii=False) + "\n"
            )
        return item

    def close_spider(self, spider):
        self.output_file.close()
