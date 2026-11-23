"""API collector orchestration: the I/O layer that pages an API and writes a raw
JSONL snapshot under ``data/downloads``. Acquisition only."""

import json
import logging
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from concurrent.futures import FIRST_COMPLETED, CancelledError, wait
from datetime import timezone
from email.utils import parsedate_to_datetime
from threading import Event, Lock

import requests

from .browserTransport import BrowserTransport
from .faceting import plan_fanout
from .models import ApiCollectionResult, ApiCollectorTask
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

def collect_profiles(
    task: ApiCollectorTask,
    records: list[dict],
    source_file_path,
) -> str:
    """Add API profiles to extracted listing records."""

    id_path = task.record_shape["id_path"]
    ids = [read_path(record, id_path) for record in records]

    if not ids or any(
        value is None or not str(value).strip()
        for value in ids
    ):
        raise ValueError(
            "Profile collection requires non-empty record IDs"
        )

    if len({str(value) for value in ids}) != len(ids):
        raise ValueError("Duplicate profile record IDs")

    if any(
        not _detail_url_for(record, task.detail)
        for record in records
    ):
        raise ValueError(
            "A profile request URL could not be resolved"
        )

    records = sorted(
        records,
        key=lambda record: str(read_path(record, id_path)),
    )

    source = Path(source_file_path)
    overview = source.with_name(
        f"{source.stem}_overview.jsonl"
    )
    members = (
        source.parent
        / "attachments"
        / "members"
        / source.stem
    )
    output = source.with_name(
        f"{source.stem}_profiles.jsonl"
    )
    pending = output.with_suffix(".jsonl.partial")

    _write_single_jsonl([records], overview)

    with _build_transport(task) as transport:
        _fetch_profiles(
            task, overview, members, transport
        )

    written = _write_single_jsonl(
        _iter_profile_records(task, records, members),
        pending,
        sort_keys=True,
    )

    if written != len(records):
        raise RuntimeError("Incomplete profile snapshot")

    pending.replace(output)
    return str(output)


def _iter_profile_records(task, records, members):
    """Decode profiles and preserve records without a profile."""

    for record in records:
        record_id = read_path(
            record, task.record_shape["id_path"]
        )
        path = members / (
            f"{_safe_member_filename(record_id)}.json"
        )

        response = json.loads(
            path.read_text(encoding="utf-8")
        )
        items = read_path(
            response, task.detail["items_path"]
        )

        if not isinstance(items, list) or len(items) > 1:
            raise ValueError(
                f"Unexpected profile response for {record_id}"
            )

        profile = {}

        if items:
            profile = read_path(
                items[0], task.detail["content_path"]
            )

            if (
                task.detail.get("content_is_json")
                and isinstance(profile, str)
            ):
                profile = json.loads(profile)

            expected_id = read_path(
                record, task.detail["id_path"]
            )
            actual_id = read_path(
                profile, task.detail["response_id_path"]
            )

            if (
                not isinstance(profile, dict)
                or actual_id is None
                or str(actual_id) != str(expected_id)
            ):
                raise ValueError(
                    f"Profile ID mismatch for {record_id}"
                )

        yield [{
            **record,
            "detail": profile,
            "profile_api_url": _detail_url_for(
                record, task.detail
            ),
            "profile_status": (
                "FOUND" if profile else "NOT_FOUND"
            ),
        }]


def collect_source(task: ApiCollectorTask) -> str:
    """Collect every page of an API source and return the snapshot path."""

    if task.endpoints and task.write_mode != "list_detail":
        raise ValueError("Multiple endpoints require write_mode=list_detail")

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


def collect_artifacts(
    task: ApiCollectorTask,
    stop_check: Optional[Callable[[List[Any]], bool]] = None,
) -> ApiCollectionResult:
    """Collect a multi-file API source and return its raw file paths.

    ``stop_check(items)``, if given, runs after each page; once it returns
    True, no further pages are requested (records already written stay).
    """

    if task.write_mode != "record_files":
        raise ValueError(
            "collect_artifacts() requires write_mode=record_files"
        )

    collected_at = datetime.now()
    transport = _build_transport(task)

    with transport:
        return _collect_record_files(task, transport, collected_at, stop_check)


def _collect_list_detail(
    task: ApiCollectorTask,
    transport: Any,
    collected_at: datetime,
) -> str:
    """Two-phase acquisition: a deduped overview JSONL, then one profile file per
    record under ``attachments/members/{id}.json``. Returns the overview path."""

    base_dir = _list_detail_base_dir(task, collected_at)
    if task.endpoints:
        base_dir = base_dir / f"run={collected_at:%Y%m%d_%H%M%S_%f}"
    timestamp = collected_at.strftime("%Y%m%d_%H%M%S")
    overview_name = task.filename or f"{task.list_name}_{timestamp}.jsonl"
    overview_path = base_dir / overview_name
    members_dir = base_dir / "attachments" / "members"

    overview_path.parent.mkdir(parents=True, exist_ok=True)

    total_hint = 0 if task.endpoints else _startup_probe(task, transport)

    overview_dedup_path = task.record_shape.get("id_path") or task.dedup_path

    pages = (
        _iter_endpoint_pages(task, transport)
        if task.endpoints else _iter_pages(task, transport)
    )

    if overview_dedup_path:
        pages = _dedup_pages(pages, overview_dedup_path)

    pages = _log_progress(pages, total_hint, f"{task.list_name} overview")

    pending_path = (
        overview_path.with_suffix(".jsonl.partial") if task.endpoints else overview_path
    )
    written = _write_single_jsonl(pages, pending_path)

    logger.info(
        f"{task.list_name}: overview wrote {written} unique records to "
        f"{overview_path}"
    )

    _check_completeness(task, written, total_hint)

    saved = _fetch_profiles(task, pending_path, members_dir, transport)

    if task.endpoints:
        pending_path.replace(overview_path)

    logger.info(f"{task.list_name}: saved {saved} profiles under {members_dir}")

    return str(overview_path)


def _iter_endpoint_pages(task: ApiCollectorTask, transport: Any) -> Iterator[List[Any]]:
    """Combine complete endpoint snapshots, preserving declared feed fields."""

    seen = set()

    for endpoint in task.endpoints:
        endpoint_task = replace(task, url=endpoint["url"], endpoints=[])
        query = build_query(endpoint_task.pagination, endpoint_task.params, 1)
        size_param = endpoint_task.pagination.get("size_param")
        if size_param:
            query[size_param] = 1
        payload = _get_json_with_retry(
            endpoint_task, endpoint_task.url, query, transport
        )
        expected = read_path(payload, endpoint_task.faceting.get("total_path", "total"))
        if type(expected) is not int or expected < 0:
            raise ValueError(f"Invalid endpoint total from {endpoint_task.url}")
        pages = _iter_pages(endpoint_task, transport)
        id_path = task.record_shape.get("id_path") or task.dedup_path

        if not id_path:
            raise ValueError("Multiple endpoints require a record ID path")

        pages = _dedup_pages(pages, id_path)
        count = 0

        for items in pages:
            for item in items:
                record_id = read_path(item, id_path)
                if record_id is None:
                    raise ValueError(f"Missing record ID from {endpoint_task.url}")
                if not _detail_url_for(item, task.detail):
                    raise ValueError(f"Missing profile URL from {endpoint_task.url}")
                if record_id in seen:
                    raise ValueError(f"Duplicate record ID across endpoints: {record_id}")
                seen.add(record_id)
            count += len(items)
            yield [{**item, **endpoint.get("record_fields", {})} for item in items]

        if count != expected:
            raise RuntimeError(
                f"Incomplete endpoint {endpoint_task.url}: collected {count} of {expected}"
            )


def _collect_record_files(
    task: ApiCollectorTask,
    transport: Any,
    collected_at: datetime,
    stop_check: Optional[Callable[[List[Any]], bool]] = None,
) -> ApiCollectionResult:
    """Write each collected API record to its own JSON file."""

    if not task.record_id_path:
        raise ValueError(
            "record_id_path is required when write_mode=record_files"
        )

    output_dir = _list_detail_base_dir(task, collected_at)
    output_dir.mkdir(parents=True, exist_ok=True)

    total_hint = _startup_probe(task, transport)
    pages = _iter_pages(task, transport, stop_check)

    if task.dedup_path:
        pages = _dedup_pages(pages, task.dedup_path)

    pages = _log_progress(pages, total_hint, task.list_name)

    file_paths = _write_record_files(
        pages=pages,
        output_dir=output_dir,
        record_id_path=task.record_id_path,
    )

    written = len(file_paths)

    logger.info(
        f"{task.list_name}: wrote {written} individual JSON files to "
        f"{output_dir}"
    )
    _check_completeness(task, written, total_hint)

    return ApiCollectionResult(
        file_paths=file_paths,
        record_count=written,
    )


def _fetch_profiles(
    task: ApiCollectorTask,
    overview_path: Path,
    members_dir: Path,
    transport: Any,
    log_every: int = 200,
) -> int:
    """Save profiles, reusing cached files; concurrency is opt-in per source."""

    rounds = task.detail.get("retry_rounds", 1)

    if type(rounds) is not int or rounds < 1:
        raise ValueError("detail.retry_rounds must be a positive integer")

    if rounds > 1:
        return _fetch_profiles_resilient(
            task, overview_path, members_dir, transport, log_every
        )

    workers = task.detail.get("max_workers", 1)
    if type(workers) is not int or workers < 1:
        raise ValueError("detail.max_workers must be a positive integer")
    if workers > 1 and (task.transport != "requests" or task.page_token):
        raise ValueError("Parallel profiles require requests without page_token")

    members_dir.mkdir(parents=True, exist_ok=True)
    id_path = task.record_shape.get("id_path")

    saved = 0
    skipped = 0
    start = time.time()

    def fetch_one(stub):
        record_id = read_path(stub, id_path) if id_path else None
        url = _detail_url_for(stub, task.detail)
        if record_id is None or not url:
            return None

        file_path = members_dir / f"{_safe_member_filename(record_id)}.json"
        if file_path.exists():
            return False

        profile = _get_json_with_retry(task, url, None, transport)

        if task.endpoints and read_path(profile, id_path) != record_id:
            raise ValueError(
                f"Profile ID does not match overview record: {record_id}"
            )

        file_path.write_text(
            json.dumps(profile, ensure_ascii=False),
            encoding="utf-8",
        )

        if task.throttle_delay:
            time.sleep(task.throttle_delay)

        return True

    def track(result):
        nonlocal saved, skipped

        if result is False:
            skipped += 1
        elif result is True:
            saved += 1

            if saved % log_every == 0:
                rate = saved / max(time.time() - start, 1e-6)
                logger.info(
                    f"{task.list_name}: {saved} profiles fetched ({rate:.1f}/s)"
                )

    logger.info("%s: profile workers=%s", task.list_name, workers)

    with overview_path.open(encoding="utf-8") as handle:
        stubs = (json.loads(line) for line in handle if line.strip())

        if workers == 1:
            for stub in stubs:
                track(fetch_one(stub))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = []

                try:
                    for stub in stubs:
                        futures.append(pool.submit(fetch_one, stub))

                    for future in as_completed(futures):
                        track(future.result())

                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise

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

    return RequestsTransport(
        headers=task.headers, timeout=task.timeout, page_token=task.page_token
    )


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
    stop_check: Optional[Callable[[List[Any]], bool]] = None,
) -> Iterator[List[Any]]:
    """Yield every page: fan-out slices when faceting is on, else param_variants,
    else a single fetch with ``params``."""

    if task.faceting.get("enabled"):
        variants = _adaptive_variants(task, transport)
    else:
        variants = task.param_variants or [{}]

    for index, variant in enumerate(variants):
        params = {**task.params, **variant}

        yield from _iter_variant_pages(task, params, transport, stop_check)

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
    stop_check: Optional[Callable[[List[Any]], bool]] = None,
) -> Iterator[List[Any]]:
    """Page one param-set until an empty (or, for a capped API, a short/over-cap)
    page, or until ``stop_check`` says we've reached already-known records.
    A ``type: "none"`` source yields one request and stops."""

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

        if stop_check is not None and stop_check(items):
            return

        if pagination_type == "none":
            return

        if pagination_type != "offset" and no_more_pages(
            fetched, page_count, cap, page_size
        ):
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

        except requests.RequestException as error:
            if attempt == task.retry:
                raise

            delay = min(
                float(task.retry_max_delay_seconds),
                float(task.retry_delay_seconds)
                * (
                    float(task.retry_backoff_multiplier)
                    ** (attempt - 1)
                ),
            )

            logger.warning(
                "%s: API request failed (%s). "
                "retry=%s/%s delay=%.1fs",
                task.list_name,
                type(error).__name__,
                attempt + 1,
                task.retry,
                delay,
            )

            if delay > 0:
                time.sleep(delay)

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
    *,
    sort_keys: bool = False,
) -> int:
    """Write every record as one raw JSON line."""

    written = 0

    with output_path.open("w", encoding="utf-8") as handle:
        for items in pages:
            for item in items:
                handle.write(json.dumps(
                    item,
                    ensure_ascii=False,
                    sort_keys=sort_keys,
                ))
                handle.write("\n")
                written += 1

    return written

def _write_record_files(
    pages: Iterable[List[Any]],
    output_dir: Path,
    record_id_path: str,
) -> List[str]:
    """Write one JSON file per unique record id and return their paths."""

    file_paths: List[str] = []
    seen_record_ids = set()

    for items in pages:
        for item in items:
            record_id = read_path(item, record_id_path)

            if record_id is None or str(record_id).strip() == "":
                raise ValueError(
                    f"Missing record id at path: {record_id_path}"
                )

            normalized_id = str(record_id).strip()

            if normalized_id in seen_record_ids:
                logger.warning(
                    f"Duplicate record id ignored: {normalized_id}"
                )
                continue

            seen_record_ids.add(normalized_id)

            filename = f"{_safe_member_filename(normalized_id)}.json"
            file_path = output_dir / filename

            file_path.write_text(
                json.dumps(item, ensure_ascii=False),
                encoding="utf-8",
            )

            file_paths.append(str(file_path))

    return file_paths

def _profile_retry_after(value):
    value = str(value or "").strip()

    if value.isdigit():
        return float(value)

    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


class _ProfileRequestGate:
    """Coordinate request spacing and cooldown across profile workers."""

    def __init__(self, transport, interval, fallback):
        self.transport = transport
        self.interval = max(0.0, float(interval))
        self.fallback = max(0.0, float(fallback))
        self.lock = Lock()
        self.stop = Event()
        self.next_at = 0.0

    def get_json(self, url, params=None):
        while True:
            if self.stop.is_set():
                raise CancelledError()

            with self.lock:
                now = time.monotonic()
                delay = self.next_at - now

                if delay <= 0:
                    self.next_at = now + self.interval
                    break

            if self.stop.wait(delay):
                raise CancelledError()

        try:
            return self.transport.get_json(url, params)

        except requests.HTTPError as error:
            response = error.response

            if response is not None and response.status_code in (429, 503):
                delay = _profile_retry_after(
                    response.headers.get("Retry-After")
                )
                if delay is None:
                    delay = self.fallback

                with self.lock:
                    self.next_at = max(
                        self.next_at,
                        time.monotonic() + delay,
                    )

                logger.warning(
                    "Profile API HTTP %s: shared cooldown %.1fs",
                    response.status_code,
                    delay,
                )

            raise


def _fetch_profiles_resilient(
    task,
    overview_path,
    members_dir,
    transport,
    log_every=200,
):
    workers = task.detail.get("max_workers", 1)
    rounds = task.detail["retry_rounds"]

    if type(workers) is not int or workers < 1:
        raise ValueError("detail.max_workers must be a positive integer")

    if task.transport != "requests" or task.page_token:
        raise ValueError(
            "Resilient profiles require requests without page_token"
        )

    required = {
        "items_path",
        "content_path",
        "id_path",
        "response_id_path",
    }
    if not required.issubset(task.detail):
        raise ValueError(
            "Resilient profiles require the enriched-profile "
            "response configuration"
        )

    members_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir = members_dir / "_pending"
    temporary_dir.mkdir(exist_ok=True)

    rows = [
        json.loads(line)
        for line in overview_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    id_path = task.record_shape["id_path"]
    failed_path = overview_path.with_name(
        f"{overview_path.stem}_failed_profiles.jsonl"
    )
    errors = {}

    def record_id(row):
        return str(read_path(row, id_path))

    def cached(row):
        path = members_dir / (
            f"{_safe_member_filename(record_id(row))}.json"
        )
        if not path.is_file():
            return False

        try:
            next(_iter_profile_records(task, [row], members_dir))
            return True
        except (ValueError, TypeError, KeyError):
            return False

    pending = [row for row in rows if not cached(row)]
    initially_cached = len(rows) - len(pending)

    gate = _ProfileRequestGate(
        transport,
        task.throttle_delay,
        task.retry_max_delay_seconds,
    )

    started = time.monotonic()
    saved = 0

    def fetch_one(row):
        url = _detail_url_for(row, task.detail)
        profile = _get_json_with_retry(task, url, None, gate)

        filename = (
            f"{_safe_member_filename(record_id(row))}.json"
        )
        temporary = temporary_dir / filename

        try:
            temporary.write_text(
                json.dumps(profile, ensure_ascii=False),
                encoding="utf-8",
            )

            # Validate using the existing profile decoder.
            next(_iter_profile_records(task, [row], temporary_dir))

            # Publish the individual checkpoint only after validation.
            temporary.replace(members_dir / filename)

        finally:
            temporary.unlink(missing_ok=True)

    logger.info(
        "%s: workers=%s total=%s cached=%s remaining=%s",
        task.list_name,
        workers,
        len(rows),
        initially_cached,
        len(pending),
    )

    try:
        for round_no in range(1, rounds + 1):
            if not pending:
                break

            gate.stop.clear()
            consecutive_failures = 0

            logger.info(
                "%s: profile round=%s/%s pending=%s",
                task.list_name,
                round_no,
                rounds,
                len(pending),
            )

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {}
                queue = iter(pending)

                def submit_one():
                    row = next(queue, None)
                    if row is not None:
                        futures[pool.submit(fetch_one, row)] = row

                try:
                    for _ in range(workers):
                        submit_one()

                    pause_round = False

                    while futures and not pause_round:
                        done, _ = wait(
                            futures,
                            return_when=FIRST_COMPLETED,
                        )

                        for future in done:
                            row = futures.pop(future)

                            try:
                                future.result()

                            except (
                                requests.RequestException,
                                ValueError,
                                TypeError,
                                KeyError,
                            ) as error:
                                response = getattr(
                                    error, "response", None
                                )
                                status = (
                                    response.status_code
                                    if response is not None
                                    else None
                                )

                                errors[record_id(row)] = {
                                    "source_record_id": record_id(row),
                                    "profile_api_url": _detail_url_for(
                                        row, task.detail
                                    ),
                                    "http_status": status,
                                    "error_type": type(error).__name__,
                                    "error": str(error),
                                }

                                logger.warning(
                                    "%s: profile %s deferred (%s, HTTP %s)",
                                    task.list_name,
                                    record_id(row),
                                    type(error).__name__,
                                    status,
                                )

                                if isinstance(
                                    error, requests.RequestException
                                ):
                                    consecutive_failures += 1
                                else:
                                    consecutive_failures = 0

                                pause_round = (
                                    status in (429, 503)
                                    or consecutive_failures
                                    >= max(3, workers)
                                )

                            else:
                                consecutive_failures = 0
                                saved += 1

                                if saved % log_every == 0:
                                    rate = saved / max(
                                        time.monotonic() - started,
                                        1e-6,
                                    )
                                    logger.info(
                                        "%s: %s new profiles fetched "
                                        "(%.1f/s)",
                                        task.list_name,
                                        saved,
                                        rate,
                                    )

                            if pause_round:
                                logger.warning(
                                    "%s: pausing this round after "
                                    "repeated source errors",
                                    task.list_name,
                                )
                                gate.stop.set()
                                for item in futures:
                                    item.cancel()
                                break

                            submit_one()

                except BaseException:
                    gate.stop.set()
                    for item in futures:
                        item.cancel()
                    raise

            pending = [row for row in rows if not cached(row)]

            reports = [
                errors.get(
                    record_id(row),
                    {
                        "source_record_id": record_id(row),
                        "profile_api_url": _detail_url_for(
                            row, task.detail
                        ),
                        "error_type": "Pending",
                        "error": "Not downloaded in this round",
                    },
                )
                for row in pending
            ]

            if reports:
                temporary = failed_path.with_suffix(
                    ".jsonl.partial"
                )
                _write_single_jsonl(
                    [reports],
                    temporary,
                    sort_keys=True,
                )
                temporary.replace(failed_path)

                if round_no < rounds:
                    logger.warning(
                        "%s: %s profiles remain; next round in "
                        "%.1fs. Failures: %s",
                        task.list_name,
                        len(pending),
                        task.retry_max_delay_seconds,
                        failed_path,
                    )
                    time.sleep(task.retry_max_delay_seconds)

        if pending:
            raise RuntimeError(
                f"{len(pending)} profiles remain unresolved; "
                "successful files are cached. "
                f"Retry the same HTML. Failures: {failed_path}"
            )

        failed_path.unlink(missing_ok=True)

        logger.info(
            "%s: all %s profiles resolved; reused=%s new=%s",
            task.list_name,
            len(rows),
            initially_cached,
            len(rows) - initially_cached,
        )

        return len(rows) - initially_cached

    finally:
        gate.stop.set()
