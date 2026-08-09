"""Choose automatic or manual acquisition for one adverse-media source."""

from pathlib import Path
from typing import Any


def acquire(
    source_config: dict[str, Any],
    mode: str | None = None,
    input_path: str | None = None,
) -> dict[str, Any]:
    acquisition = source_config["acquisition"]
    selected_mode = mode or acquisition["mode"]

    if selected_mode not in {"automatic", "manual"}:
        raise ValueError("mode must be automatic or manual")

    if selected_mode == "manual":
        manual_path = input_path or acquisition.get("manual_path")
        if not manual_path:
            raise ValueError("input_path or acquisition.manual_path is required")

        return {
            "mode": "manual",
            "status": "waiting_for_manual_files",
            "input_path": str(Path(manual_path)),
            "files": [],
            "downloaded_article_count": 0,
            "error_count": 0,
        }

    method = acquisition["method"]
    if method == "crawler":
        # Lazy import keeps manual mode independent from Scrapy/Playwright.
        from ingestion.crawler.crawlerService import run_article_crawler

        result = run_article_crawler(source_config)
        return {"mode": "automatic", **result}

    raise NotImplementedError(
        f"Automatic {method} is outside this crawler-only change; "
        "the existing API collector/downloader should remain unchanged"
    )
