"""
API collector orchestration (the I/O layer).

Drives the page loop using the pure helpers in ``pagination.py``, then writes
the collected records as a raw JSONL snapshot under the standard
``data/downloads`` convention. It performs acquisition only -- no mapping,
normalization, entity detection, or database work.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, List

import requests

from .models import ApiCollectorTask
from .pagination import build_query, extract_items, should_stop


ROOT_DIR = Path(__file__).resolve().parents[2]
DOWNLOAD_ROOT = ROOT_DIR / "data" / "downloads"


def collect_source(task: ApiCollectorTask) -> str:
    """
    Collect every page of an API source and write a snapshot to disk.

    Returns the path of the written snapshot.
    """

    collected_at = datetime.now()

    output_path = _build_output_path(
        task=task,
        collected_at=collected_at,
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    pages = _iter_pages(task)

    if task.write_mode == "single_jsonl":
        _write_single_jsonl(pages, output_path)
    elif task.write_mode == "per_entity":
        _write_per_entity(pages, output_path.parent)
    else:
        raise ValueError(
            f"Unsupported write_mode: {task.write_mode}"
        )

    return str(output_path)


def _iter_pages(task: ApiCollectorTask) -> Iterator[List[Any]]:
    """
    Yield each page's list of records until the API returns an empty page.

    No page limit: paging ends on the first empty page. A source declared
    ``type: "none"`` is not paged at all -- it yields a single request's
    records and stops.
    """

    pagination_type = task.pagination.get("type", "page")
    page = task.pagination.get("start_page", 1)

    while True:
        query = build_query(
            pagination=task.pagination,
            params=task.params,
            page=page,
        )

        payload = _get_page(task, query)

        items = extract_items(payload, task.items_path)

        if should_stop(items):
            return

        yield items

        # A single-request source (``type: "none"``) has no next page: one
        # fetch is the whole dataset, so stop instead of re-requesting it.
        if pagination_type == "none":
            return

        page += 1

        if task.throttle_delay:
            time.sleep(task.throttle_delay)


def _get_page(task: ApiCollectorTask, query: dict) -> Any:
    """
    Fetch and parse one page of JSON, retrying on request errors.
    """

    headers = task.headers or {
        "User-Agent": "Mozilla/5.0",
    }

    for attempt in range(1, task.retry + 1):
        try:
            response = requests.get(
                task.url,
                params=query,
                headers=headers,
                timeout=task.timeout,
            )

            # An auth failure will not fix itself on retry, and a silent empty
            # result would look like "no records". Fail loudly and immediately
            # so a rotated/expired key/token is obvious.
            if response.status_code in (401, 403):
                raise RuntimeError(
                    f"API authentication failed ({response.status_code}) for "
                    f"{task.url}. The API key/token was rejected -- it may have "
                    f"rotated; update the source's api_config headers "
                    f"(e.g. 'x-api-key') in the watchlist config."
                )

            response.raise_for_status()

            return response.json()

        except requests.RequestException:
            if attempt == task.retry:
                raise

    raise RuntimeError(
        f"Failed to collect API page: {task.url} {query}"
    )


def _build_output_path(
    task: ApiCollectorTask,
    collected_at: datetime,
) -> Path:
    """
    Build the dated snapshot path, matching the downloader's convention:
    ``data/downloads/{source}/{list}/year=/month=/day=/{list}_{ts}.jsonl``.
    """

    download_root = (
        Path(task.download_dir)
        if task.download_dir
        else DOWNLOAD_ROOT
    )

    directory = (
        download_root
        / task.source_name
        / task.list_name
        / f"year={collected_at:%Y}"
        / f"month={collected_at:%m}"
        / f"day={collected_at:%d}"
    )

    timestamp = collected_at.strftime("%Y%m%d_%H%M%S")

    filename = (
        task.filename
        or f"{task.list_name}_{timestamp}.jsonl"
    )

    return directory / filename


def _write_single_jsonl(
    pages: Iterable[List[Any]],
    output_path: Path,
) -> None:
    """
    Write every record as one raw JSON line into a single JSONL file.

    Streams page by page, so memory stays flat regardless of dataset size.
    """

    with output_path.open("w", encoding="utf-8") as handle:
        for items in pages:
            for item in items:
                handle.write(
                    json.dumps(item, ensure_ascii=False)
                )
                handle.write("\n")


def _write_per_entity(
    pages: Iterable[List[Any]],
    output_dir: Path,
) -> None:
    """
    Write one file per record (adverse-media mode).

    Extension point for future adverse-media sources; not implemented yet.
    """

    raise NotImplementedError(
        "write_mode 'per_entity' (adverse media) is not implemented yet"
    )
