"""Validation and JSONL writers shared by crawler and manual runs."""

import json
from pathlib import Path
from typing import Any, Iterable

from scrapy.exceptions import DropItem

from parsing.htmlParser import get_nested_value


COMMON_REQUIRED_ARTICLE_PATHS = (
    "source.id",
    "source.name",
    "source.section",
    "classification.record_type",
    "content.title",
)

AUTOMATIC_REQUIRED_ARTICLE_PATHS = (
    "urls.article",
)


def has_value(value: Any) -> bool:
    return value not in (None, "", [], {})


def validate_article_record(record: dict[str, Any], automatic: bool) -> None:
    required_paths = list(COMMON_REQUIRED_ARTICLE_PATHS)
    if automatic:
        required_paths.extend(AUTOMATIC_REQUIRED_ARTICLE_PATHS)

    for field_path in required_paths:
        if not has_value(get_nested_value(record, field_path)):
            raise ValueError(f"Missing required article field: {field_path}")


def deduplication_key(record: dict[str, Any], paths: Iterable[str]) -> str | None:
    for path in paths:
        value = get_nested_value(record, path)
        if has_value(value):
            return f"{path}:{value}"
    return None


class ArticleRecordWriter:
    """Write nested article records in a crawler-independent way."""

    def __init__(
        self,
        output_path: str | Path,
        mode: str,
        deduplication_paths: Iterable[str],
    ):
        self.output_path = Path(output_path)
        self.mode = mode
        self.deduplication_paths = tuple(deduplication_paths)
        self.output_file = None
        self.seen_keys: set[str] = set()

    def open(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "a" and self.output_path.is_file():
            with self.output_path.open(encoding="utf-8") as existing_file:
                for line_number, line in enumerate(existing_file, start=1):
                    if not line.strip():
                        continue
                    try:
                        existing_record = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise ValueError(
                            f"Invalid JSONL at {self.output_path}:{line_number}"
                        ) from error
                    key = deduplication_key(
                        existing_record, self.deduplication_paths
                    )
                    if key:
                        self.seen_keys.add(key)

        self.output_file = self.output_path.open(self.mode, encoding="utf-8")

    def write(self, record: dict[str, Any]) -> bool:
        if self.output_file is None:
            raise RuntimeError("ArticleRecordWriter is not open")

        key = deduplication_key(record, self.deduplication_paths)
        if key and key in self.seen_keys:
            return False

        self.output_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        if key:
            self.seen_keys.add(key)
        return True

    def close(self) -> None:
        if self.output_file is not None:
            self.output_file.close()
            self.output_file = None


class ArticleValidationPipeline:
    def process_item(self, item, spider):
        try:
            validate_article_record(item["record"], automatic=True)
        except ValueError as error:
            raise DropItem(str(error)) from error
        return item


class ArticleJsonlPipeline:
    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        pipeline.crawler = crawler
        return pipeline

    def open_spider(self, spider):
        output_path = Path(spider.source_config["output"]["path"])
        if not output_path.is_absolute():
            output_path = spider.project_root / output_path

        file_mode = "w" if spider.run_type == "initial" else "a"
        paths = spider.source_config.get("deduplication", {}).get(
            "keys", ["source.record_id", "urls.article"]
        )
        self.writer = ArticleRecordWriter(output_path, file_mode, paths)
        self.writer.open()

    def process_item(self, item, spider):
        if not self.writer.write(item["record"]):
            raise DropItem("Duplicate article")
        return item

    def close_spider(self, spider):
        self.writer.close()


class WatchlistJsonlPipeline:
    def open_spider(self, spider):
        output_path = Path(spider.source_config["output"]["path"])
        if not output_path.is_absolute():
            output_path = spider.project_root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_file = output_path.open("w", encoding="utf-8")

    def process_item(self, item, spider):
        self.output_file.write(json.dumps(dict(item["payload"]), ensure_ascii=False) + "\n")
        return item

    def close_spider(self, spider):
        self.output_file.close()
