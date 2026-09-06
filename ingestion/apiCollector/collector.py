"""API collector orchestration: the I/O layer that pages an API and writes a raw
JSONL snapshot under ``data/downloads``. Acquisition only."""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, List

import requests

from .browserTransport import BrowserTransport
from .faceting import plan_fanout
from .models import ApiCollectorTask
from .pagination import (
    build_query,
    extract_items,
    no_more_pages,
    read_path,
    should_stop,
)
from .transport import RequestsTransport


logger = logging.getLogger(__name__)


ROOT_DIR = Path(__file__).resolve().parents[2]
DOWNLOAD_ROOT = ROOT_DIR / "data" / "downloads"


def collect_source(task: ApiCollectorTask) -> str:
    """Collect every page of an API source and return the snapshot path."""

    collected_at = datetime.now()
    transport = _build_transport(task)

    with transport:
        if task.write_mode == "list_detail":
            return _collect_list_detail(task, transport, collected_at)

        if task.write_mode != "single_jsonl":
            raise ValueError(f"Unsupported write_mode: {task.write_mode}")

        output_path = _build_output_path(task=task, collected_at=collected_at)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        total_hint = _startup_probe(task, transport)

        pages = _iter_pages(task, transport)

        if task.dedup_path:
            pages = _dedup_pages(pages, task.dedup_path)

        pages = _log_progress(pages, total_hint, task.list_name)

        written = _write_single_jsonl(pages, output_path)
        logger.info(
            f"{task.list_name}: wrote {written} records to {output_path}"
        )
        _check_completeness(task, written, total_hint)

    return str(output_path)


def _collect_list_detail(
    task: ApiCollectorTask,
    transport: Any,
    collected_at: datetime,
) -> str:
    """Two-phase acquisition: a deduped overview JSONL, then one profile file per
    record under ``attachments/members/{id}.json``. Returns the overview path."""

    base_dir = _list_detail_base_dir(task, collected_at)
    timestamp = collected_at.strftime("%Y%m%d_%H%M%S")
    overview_name = task.filename or f"{task.list_name}_{timestamp}.jsonl"
    overview_path = base_dir / overview_name
    members_dir = base_dir / "attachments" / "members"

    overview_path.parent.mkdir(parents=True, exist_ok=True)

    total_hint = _startup_probe(task, transport)

    overview_dedup_path = task.record_shape.get("id_path") or task.dedup_path

    pages = _iter_pages(task, transport)

    if overview_dedup_path:
        pages = _dedup_pages(pages, overview_dedup_path)

    pages = _log_progress(pages, total_hint, f"{task.list_name} overview")

    written = _write_single_jsonl(pages, overview_path)

    logger.info(
        f"{task.list_name}: overview wrote {written} unique records to "
        f"{overview_path}"
    )

    _check_completeness(task, written, total_hint)

    saved = _fetch_profiles(task, overview_path, members_dir, transport)

    logger.info(f"{task.list_name}: saved {saved} profiles under {members_dir}")

    return str(overview_path)


def _fetch_profiles(
    task: ApiCollectorTask,
    overview_path: Path,
    members_dir: Path,
    transport: Any,
    log_every: int = 200,
) -> int:
    """Save each record's profile JSON; skip one already on disk (resumable)."""

    members_dir.mkdir(parents=True, exist_ok=True)

    id_path = task.record_shape.get("id_path")

    saved = 0
    skipped = 0
    start = time.time()

    with overview_path.open(encoding="utf-8") as handle:
        for line in handle:
            stub = json.loads(line)

            record_id = read_path(stub, id_path) if id_path else None
            url = _detail_url_for(stub, task.detail)

            if record_id is None or not url:
                continue

            file_path = members_dir / f"{_safe_member_filename(record_id)}.json"

            if file_path.exists():
                skipped += 1
                continue

            profile = _get_json_with_retry(task, url, None, transport)

            file_path.write_text(
                json.dumps(profile, ensure_ascii=False),
                encoding="utf-8",
            )

            saved += 1

            if saved % log_every == 0:
                rate = saved / max(time.time() - start, 1e-6)
                logger.info(
                    f"{task.list_name}: {saved} profiles fetched ({rate:.1f}/s)"
                )

            if task.throttle_delay:
                time.sleep(task.throttle_delay)

    if skipped:
        logger.info(
            f"{task.list_name}: skipped {skipped} profiles already on disk"
        )

    return saved


def _list_detail_base_dir(
    task: ApiCollectorTask,
    collected_at: datetime,
) -> Path:
    """Date-partitioned base directory for a list_detail run."""

    download_root = (
        Path(task.download_dir) if task.download_dir else DOWNLOAD_ROOT
    )

    return (
        download_root
        / task.source_name
        / task.list_name
        / f"year={collected_at:%Y}"
        / f"month={collected_at:%m}"
        / f"day={collected_at:%d}"
    )


def _safe_member_filename(record_id: Any) -> str:
    """Make a record id safe as a file name (Interpol ids carry a '/')."""

    text = str(record_id)

    for char in '/\\:*?"<>|':
        text = text.replace(char, "-")

    return text


def _build_transport(task: ApiCollectorTask):
    """Pick the transport that fetches this source's JSON."""

    if task.transport == "browser":
        return BrowserTransport(task.bypass_config)

    return RequestsTransport(headers=task.headers, timeout=task.timeout)


def _dedup_pages(
    pages: Iterable[List[Any]],
    dedup_path: str,
) -> Iterator[List[Any]]:
    """Drop records whose key at ``dedup_path`` was already seen (keyless kept)."""

    seen = set()

    for items in pages:
        kept = []

        for item in items:
            key = read_path(item, dedup_path)

            if key is not None and key in seen:
                continue

            if key is not None:
                seen.add(key)

            kept.append(item)

        if kept:
            yield kept


def _iter_pages(
    task: ApiCollectorTask,
    transport: Any,
) -> Iterator[List[Any]]:
    """Yield every page: fan-out slices when faceting is on, else param_variants,
    else a single fetch with ``params``."""

    if task.faceting.get("enabled"):
        variants = _adaptive_variants(task, transport)
    else:
        variants = task.param_variants or [{}]

    for index, variant in enumerate(variants):
        params = {**task.params, **variant}

        yield from _iter_variant_pages(task, params, transport)

        if task.throttle_delay and index < len(variants) - 1:
            time.sleep(task.throttle_delay)


def _make_get_total(task: ApiCollectorTask, transport: Any):
    """Build ``get_total(params)``: one "how many?" query against the endpoint."""

    total_path = task.faceting.get("total_path", "total")
    page_param = task.pagination.get("page_param", "page")
    size_param = task.pagination.get("size_param")

    def get_total(facet_params: dict) -> int:
        query = {**task.params, **facet_params, page_param: 1}

        if size_param:
            query[size_param] = 1

        payload = _get_json_with_retry(task, task.url, query, transport)

        total = read_path(payload, total_path) or 0

        logger.info(
            f"{task.list_name}: total for {facet_params or 'root'} = {total}"
        )

        return total

    return get_total


def _build_plan(task: ApiCollectorTask, transport: Any):
    """Run the fan-out planner against the live "how many?" endpoint."""

    cap = task.faceting.get("cap", 160)
    facets = task.faceting.get("facets", [])

    return plan_fanout(_make_get_total(task, transport), {}, cap, facets)


def _adaptive_variants(
    task: ApiCollectorTask,
    transport: Any,
) -> List[dict]:
    """Compute the fan-out slices; raise if any stays over the cap after every
    facet (fetching would silently drop records)."""

    plan = _build_plan(task, transport)

    logger.info(
        f"{task.list_name}: adaptive fan-out produced {len(plan.leaves)} "
        f"slices (root total {plan.root_total})"
    )

    if plan.unresolved:
        example = plan.unresolved[0]
        cap = task.faceting.get("cap", 160)
        facets = task.faceting.get("facets", [])

        raise RuntimeError(
            f"{task.list_name}: {len(plan.unresolved)} slice(s) still exceed "
            f"the cap ({cap}) after all {len(facets)} facets -- fetching would "
            f"drop records. Deepen a name facet (raise max_depth) or add a facet "
            f"type. Example over-cap slice: {example['params']} "
            f"= {example['total']} records."
        )

    return plan.leaves


def _startup_probe(task: ApiCollectorTask, transport: Any) -> int:
    """Return the dataset total for the progress ETA; warn if the site's real cap
    dropped below the configured one. Faceted sources only, else 0."""

    if not task.faceting.get("enabled"):
        return 0

    cap = task.faceting.get("cap", 160)
    total_path = task.faceting.get("total_path", "total")
    page_param = task.pagination.get("page_param", "page")
    size_param = task.pagination.get("size_param")

    query = {**task.params, page_param: 1}

    if size_param:
        query[size_param] = cap

    try:
        payload = _get_json_with_retry(task, task.url, query, transport)
    except Exception:
        return 0

    total = read_path(payload, total_path) or 0
    got = len(extract_items(payload, task.items_path))

    if total > got and got < cap:
        logger.warning(
            f"{task.list_name}: the site returned only {got} records for a page "
            f"of {cap} while {total} exist -- lower faceting.cap to {got}."
        )

    return total


def _check_completeness(
    task: ApiCollectorTask,
    written: int,
    total_hint: int,
) -> None:
    """Warn if what we wrote falls short of the API's reported total."""

    if total_hint <= 0:
        return

    gap = total_hint - written
    tolerance = max(10, int(total_hint * 0.005))

    if gap > tolerance:
        logger.warning(
            f"{task.list_name}: collected {written} of {total_hint} (missing "
            f"{gap}). Add another facet, or lower faceting.cap."
        )
    else:
        logger.info(
            f"{task.list_name}: completeness OK -- {written} of {total_hint}."
        )


def _log_progress(
    pages: Iterable[List[Any]],
    total_hint: int,
    list_name: str,
    every: int = 200,
) -> Iterator[List[Any]]:
    """Pass pages through unchanged while logging throughput and an ETA."""

    start = time.time()
    count = 0
    next_mark = every

    for items in pages:
        count += len(items)

        if count >= next_mark:
            logger.info(_progress_line(count, total_hint, start))

            while next_mark <= count:
                next_mark += every

        yield items

    elapsed = time.time() - start

    logger.info(f"done: {count} records in {_format_duration(elapsed)}")


def _progress_line(count: int, total_hint: int, start: float) -> str:
    """Build one progress line: count, percent (if known), rate, and ETA."""

    elapsed = max(time.time() - start, 1e-6)
    rate = count / elapsed

    if total_hint > 0:
        percent = 100.0 * count / total_hint
        remaining = max(total_hint - count, 0)
        eta = remaining / rate if rate > 0 else 0

        return (
            f"progress: {count}/{total_hint} ({percent:.1f}%) | "
            f"{rate:.1f} rec/s | ETA ~{_format_duration(eta)}"
        )

    return f"progress: {count} records | {rate:.1f} rec/s"


def _format_duration(seconds: float) -> str:
    """Human-friendly duration, e.g. '8m20s' or '45s'."""

    seconds = int(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)

    if hours:
        return f"{hours}h{minutes:02d}m"

    if minutes:
        return f"{minutes}m{secs:02d}s"

    return f"{secs}s"


def _iter_variant_pages(
    task: ApiCollectorTask,
    params: dict,
    transport: Any,
) -> Iterator[List[Any]]:
    """Page one param-set until an empty (or, for a capped API, a short/over-cap)
    page. A ``type: "none"`` source yields one request and stops."""

    pagination_type = task.pagination.get("type", "page")
    page = task.pagination.get("start_page", 1)
    page_size = task.pagination.get("page_size")
    cap = task.faceting.get("cap")
    fetched = 0

    while True:
        query = build_query(
            pagination=task.pagination,
            params=params,
            page=page,
        )

        payload = _get_page(task, query, transport)

        items = extract_items(payload, task.items_path)
        page_count = len(items)

        if should_stop(items):
            return

        yield items

        fetched += page_count

        if pagination_type == "none":
            return

        if no_more_pages(fetched, page_count, cap, page_size):
            return

        page += 1

        if task.throttle_delay:
            time.sleep(task.throttle_delay)


def _detail_url_for(stub: dict, detail_cfg: dict):
    """Resolve a stub's detail URL from ``url_path``, else ``id_path`` +
    ``url_template``. None when unresolvable."""

    url_path = detail_cfg.get("url_path")

    if url_path:
        return read_path(stub, url_path)

    id_value = read_path(stub, detail_cfg["id_path"])

    if not id_value:
        return None

    return detail_cfg["url_template"].format(id=id_value)


def _get_page(task: ApiCollectorTask, query: dict, transport: Any) -> Any:
    """Fetch and parse one page of JSON, retrying on request errors."""

    return _get_json_with_retry(task, task.url, params=query, transport=transport)


def _get_json_with_retry(
    task: ApiCollectorTask,
    url: str,
    params: Any,
    transport: Any,
) -> Any:
    """Fetch one JSON document, retrying on request errors; a RuntimeError (e.g.
    a 401/403 block) is raised immediately, not retried."""

    for attempt in range(1, task.retry + 1):
        try:
            return transport.get_json(url, params)

        except requests.RequestException:
            if attempt == task.retry:
                raise

    raise RuntimeError(f"Failed to collect API page: {url} {params}")


def _build_output_path(
    task: ApiCollectorTask,
    collected_at: datetime,
) -> Path:
    """Build the dated snapshot path, matching the downloader's convention."""

    download_root = (
        Path(task.download_dir) if task.download_dir else DOWNLOAD_ROOT
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

    filename = task.filename or f"{task.list_name}_{timestamp}.jsonl"

    return directory / filename


def _write_single_jsonl(
    pages: Iterable[List[Any]],
    output_path: Path,
) -> int:
    """Write every record as one raw JSON line; return the number written."""

    written = 0

    with output_path.open("w", encoding="utf-8") as handle:
        for items in pages:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False))
                handle.write("\n")
                written += 1

    return written
