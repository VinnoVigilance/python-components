"""Orchestrate automatic or manual adverse-media ingestion."""

import hashlib
import logging
import sys
from copy import deepcopy
from pathlib import Path
from pprint import pprint
from time import perf_counter
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from scrapy.crawler import CrawlerProcess  # noqa: E402
from scrapy.settings import Settings  # noqa: E402

from config.loggingConfig import configure_logging  # noqa: E402
from ingestion.crawler import settings as crawler_settings  # noqa: E402
from ingestion.crawler.configLoader import load_source_config  # noqa: E402
from ingestion.crawler.pipelines import (  # noqa: E402
    ArticleRecordWriter,
    validate_article_record,
)
from ingestion.crawler.spiders.genericArticleSpider import GenericArticleSpider  # noqa: E402
from ingestion.downloader.interface import resolve_manual_files  # noqa: E402
from parsing.htmlParser import HtmlParser, set_nested_value  # noqa: E402
from parsing.pdfParser import PdfParser  # noqa: E402


logger = logging.getLogger(__name__)


def run_adverse_media_pipeline(
    source_name: str,
    mode: str = "automatic",
    run_type: str = "incremental",
    input_path: str | None = None,
) -> dict[str, Any]:
    if mode not in {"automatic", "manual"}:
        raise ValueError("mode must be automatic or manual")
    if run_type not in {"initial", "incremental"}:
        raise ValueError("run_type must be initial or incremental")

    config = load_source_config(source_name)
    supported_modes = config["acquisition"].get("supported_modes", [])
    if supported_modes and mode not in supported_modes:
        raise ValueError(f"Mode {mode} is not supported by source {source_name}")

    if mode == "manual":
        return _run_manual(config, input_path, run_type)

    method = config["acquisition"]["method"]
    if method != "crawler":
        raise NotImplementedError(
            "This adverse-media entry point currently implements automatic "
            "crawler sources. API/downloader interfaces are ready for source-specific mapping."
        )
    return _run_automatic_crawler(source_name, config, run_type)


def _run_automatic_crawler(source_name, config, run_type):
    started_at = perf_counter()
    scrapy_settings = Settings()
    scrapy_settings.setmodule(crawler_settings, priority="project")

    if config["acquisition"].get("engine") == "playwright":
        _enable_playwright(scrapy_settings)

    crawler_process = CrawlerProcess(settings=scrapy_settings, install_root_handler=False)
    crawler = crawler_process.create_crawler(GenericArticleSpider)
    crawler_process.crawl(crawler, source=source_name, run_type=run_type)
    crawler_process.start()
    stats = crawler.stats.get_stats()
    return {
        "source": source_name,
        "mode": "automatic",
        "run_type": run_type,
        "pipeline_result": str(stats.get("finish_reason", "unknown")).upper(),
        "exported_article_count": stats.get("item_scraped_count", 0),
        "stored_raw_article_count": stats.get("raw_storage/record_count", 0),
        "dropped_article_count": stats.get("item_dropped_count", 0),
        "error_count": stats.get("log_count/ERROR", 0),
        "elapsed_seconds": round(perf_counter() - started_at, 2),
    }


def _run_manual(config, input_path, run_type):
    if not input_path:
        raise ValueError("input_path is required in manual mode")
    allowed = config["acquisition"]["manual"]["allowed_file_types"]
    files = resolve_manual_files(input_path, allowed)
    output_path = Path(config["output"]["path"])
    if not output_path.is_absolute():
        output_path = ROOT_DIR / output_path
    file_mode = "w" if run_type == "initial" else "a"
    dedup_paths = config.get("deduplication", {}).get(
        "keys", ["source.record_id", "urls.article"]
    )
    writer = ArticleRecordWriter(output_path, file_mode, dedup_paths)
    html_parser = HtmlParser()
    pdf_parser = PdfParser()
    processed = 0
    skipped = 0
    writer.open()
    try:
        for file_path in files:
            extension = file_path.suffix.lower().lstrip(".")
            if extension in {"html", "htm"}:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                record = html_parser.parse_content(content, config, "article")
            elif extension == "pdf":
                record = deepcopy(config["defaults"])
                set_nested_value(record, "content.title", file_path.stem)
                set_nested_value(record, "content.article_text", pdf_parser.parse_text(file_path))
            else:
                continue

            set_nested_value(record, "source.record_id", _file_record_id(file_path))
            validate_article_record(record, automatic=False)
            if writer.write(record):
                processed += 1
            else:
                skipped += 1
    finally:
        writer.close()

    return {
        "source": config["source"]["id"],
        "mode": "manual",
        "run_type": run_type,
        "processed_article_count": processed,
        "skipped_article_count": skipped,
        "output_path": str(output_path),
    }


def _file_record_id(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:20]


def _enable_playwright(settings):
    try:
        import scrapy_playwright  # noqa: F401
    except ImportError as error:
        raise RuntimeError(
            "Playwright engine requires scrapy-playwright and playwright"
        ) from error
    settings.set(
        "DOWNLOAD_HANDLERS",
        {
            "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        priority="project",
    )
    settings.set(
        "TWISTED_REACTOR",
        "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
        priority="project",
    )


if __name__ == "__main__":
    configure_logging()
    try:
        pprint(
            run_adverse_media_pipeline(
                source_name="nbi",
                mode="automatic",
                run_type="incremental",
            )
        )
    except Exception:
        logger.exception("Adverse-media pipeline execution failed")
        raise
