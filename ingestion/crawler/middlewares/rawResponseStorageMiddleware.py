"""Optional raw-response storage for crawler types that explicitly enable it.

Adverse-media articles use ``AdverseMediaHtmlStoragePipeline`` instead, so this
middleware is intentionally not enabled in the shared settings.
"""

import re
from pathlib import Path


SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class RawResponseStorageMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls()
        middleware.crawler = crawler
        return middleware

    def process_response(self, request, response, spider):
        if not request.meta.get("save_raw_response", False):
            return response
        if not 200 <= response.status < 300:
            return response

        storage = getattr(spider, "source_config", {}).get("storage", {})
        if not storage.get("save_raw_response", False):
            return response

        source = spider.source_config.get("source", {})
        run_time = spider.run_started_at
        scope = request.meta.get("storage_scope")
        extension = self._detect_extension(response)

        values = {
            "source_id": source.get("id", "unknown"),
            "section": source.get("section", source.get("list_name", "unknown")),
            "year": run_time.strftime("%Y"),
            "month": run_time.strftime("%m"),
            "day": run_time.strftime("%d"),
            "run_id": spider.run_id,
            "extension": extension,
        }

        if scope == "record":
            record_key = self._safe_name(request.meta.get("record_key", "record"))
            values["record_key"] = record_key
            filename = storage.get(
                "filename_template", "{record_key}.{extension}"
            ).format(**values)
        elif scope == "page":
            page_number = int(request.meta.get("page_number", 1))
            values["page"] = page_number
            filename = f"page_{page_number:04d}.{extension}"
        else:
            return response

        root_directory = Path(storage.get("root_directory", "data/downloads"))
        if not root_directory.is_absolute():
            root_directory = spider.project_root / root_directory

        directory_template = storage.get(
            "directory_template",
            "{source_id}/{section}/year={year}/month={month}/day={day}/run={run_id}",
        )
        raw_directory = root_directory / directory_template.format(**values)
        raw_directory.mkdir(parents=True, exist_ok=True)
        raw_path = raw_directory / filename
        raw_path.write_bytes(response.body)

        request.meta["raw_file_path"] = str(raw_path)
        self.crawler.stats.inc_value(f"raw_storage/{scope}_count")
        return response

    @staticmethod
    def _detect_extension(response) -> str:
        content_type = response.headers.get(b"Content-Type", b"").decode().lower()
        if "application/pdf" in content_type:
            return "pdf"
        if "application/json" in content_type:
            return "json"
        if "xml" in content_type or "rss" in content_type:
            return "xml"
        return "html"

    @staticmethod
    def _safe_name(value: object) -> str:
        sanitized = SAFE_NAME.sub("_", str(value)).strip("._")
        return sanitized or "record"
