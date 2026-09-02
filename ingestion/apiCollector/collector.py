"""
API collector orchestration (the I/O layer).

Drives the page loop using the pure helpers in ``pagination.py``, then writes
the collected records as a raw JSONL snapshot under the standard
``data/downloads`` convention. It performs acquisition only -- no mapping,
normalization, entity detection, or database work.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, List

import requests

from .faceting import plan_fanout
from .models import ApiCollectorTask
from .pagination import (
    assemble_record,
    build_detail_url,
    build_query,
    extract_items,
    merge_records,
    no_more_pages,
    read_path,
    should_stop,
)
from .transport import RequestsTransport


logger = logging.getLogger(__name__)


ROOT_DIR = Path(__file__).resolve().parents[2]
DOWNLOAD_ROOT = ROOT_DIR / "data" / "downloads"


def collect_source(task: ApiCollectorTask) -> str:
    """
    Collect every page of an API source and write a snapshot to disk.

    Returns the path of the written snapshot.
    """

    collected_at = datetime.now()

    # The transport is opened once for the whole run (the browser transport
    # keeps a single warm session across every page and detail fetch) and
    # closed when collection finishes.
    transport = _build_transport(task)

    with transport:
        # "list_detail" writes its own CFTC-style layout -- a primary listing
        # file plus one profile file per person -- so it owns its paths and its
        # two phases; hand off before the single-file machinery below.
        if task.write_mode == "list_detail":
            return _collect_list_detail(task, transport, collected_at)

        output_path = _build_output_path(
            task=task,
            collected_at=collected_at,
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # A total up front (from the API's reported count) lets the progress
        # logger show a percentage and an ETA while the long detail phase runs,
        # and lets us detect a changed site cap.
        total_hint = _startup_probe(task, transport)

        pages = _iter_pages(task, transport)

        if task.dedup_path:
            pages = _dedup_pages(pages, task.dedup_path)

        pages = _log_progress(pages, total_hint, task.list_name)

        if task.write_mode == "single_jsonl":
            written = _write_single_jsonl(pages, output_path)
            logger.info(
                f"{task.list_name}: wrote {written} records to {output_path}"
            )
            _check_completeness(task, written, total_hint)
        elif task.write_mode == "per_entity":
            _write_per_entity(pages, output_path.parent)
        else:
            raise ValueError(
                f"Unsupported write_mode: {task.write_mode}"
            )

    return str(output_path)


def _collect_list_detail(
    task: ApiCollectorTask,
    transport: Any,
    collected_at: datetime,
) -> str:
    """
    Two-phase CFTC-style acquisition for a capped list+detail API.

    Phase A (overview): run the fan-out, fetch only the listing pages (no
    profile hydration), dedup to unique records, and write them as one primary
    JSONL -- ``data/downloads/{source}/{list}/.../{list}.jsonl``.

    Phase B (profiles): stream that overview file and, for each record, follow
    its detail URL and save the whole profile JSON as its own file under
    ``attachments/members/{id}.json`` -- one file per person, linked back to the
    overview by id (the same layout the crawler's ``CrawlerStorage`` produces).
    Sequential by design (the browser transport has no parallel fetch), and
    resumable: a profile whose file already exists is skipped.

    Returns the overview (primary) file path.
    """

    base_dir = _list_detail_base_dir(task, collected_at)
    # Timestamp the overview like the single_jsonl/FBI snapshots
    # ({list}_{ts}.jsonl); the per-person profiles stay in the day-level
    # attachments/members/ dir, keyed and deduped by id across runs.
    timestamp = collected_at.strftime("%Y%m%d_%H%M%S")
    overview_name = task.filename or f"{task.list_name}_{timestamp}.jsonl"
    overview_path = base_dir / overview_name
    members_dir = base_dir / "attachments" / "members"

    overview_path.parent.mkdir(parents=True, exist_ok=True)

    total_hint = _startup_probe(task, transport)

    # --- Phase A: listing only, deduped to unique people ---
    # The raw list stub has no assembled "source_record_id" yet, so dedup on the
    # stub's own id path (e.g. "entity_id") rather than task.dedup_path.
    overview_dedup_path = task.record_shape.get("id_path") or task.dedup_path

    pages = _iter_pages(task, transport, hydrate=False)

    if overview_dedup_path:
        pages = _dedup_pages(pages, overview_dedup_path)

    pages = _log_progress(pages, total_hint, f"{task.list_name} overview")

    written = _write_single_jsonl(pages, overview_path)

    logger.info(
        f"{task.list_name}: overview wrote {written} unique records to "
        f"{overview_path}"
    )

    _check_completeness(task, written, total_hint)

    # --- Phase B: one raw-profile file per person ---
    saved = _fetch_profiles(task, overview_path, members_dir, transport)

    logger.info(
        f"{task.list_name}: saved {saved} profiles under {members_dir}"
    )

    return str(overview_path)


def _fetch_profiles(
    task: ApiCollectorTask,
    overview_path: Path,
    members_dir: Path,
    transport: Any,
    log_every: int = 200,
) -> int:
    """
    Fetch and save each person's profile listed in the overview file.

    For every record it resolves the detail URL, fetches the profile JSON
    sequentially through the warm transport, and writes it verbatim to
    ``attachments/members/{id}.json``. A record whose file already exists is
    skipped, so an interrupted run resumes without re-fetching.
    """

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
    """
    Date-partitioned base directory for a list_detail run, matching the
    downloader/crawler convention:
    ``data/downloads/{source}/{list}/year=/month=/day=/``.
    """

    download_root = (
        Path(task.download_dir)
        if task.download_dir
        else DOWNLOAD_ROOT
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
    """
    Make a record id safe as a file name. Interpol ids carry a '/'
    (e.g. "2024/64373"), illegal in a path, so path-unsafe characters are
    replaced with '-' -> "2024-64373". The record keeps its real id inside.
    """

    text = str(record_id)

    for char in '/\\:*?"<>|':
        text = text.replace(char, "-")

    return text


def _build_transport(task: ApiCollectorTask):
    """
    Pick the transport that fetches this source's JSON.

    "browser" is imported lazily so a run that only touches open APIs never
    loads the stealth browser (and its heavy dependencies).
    """

    if task.transport == "browser":
        from ingestion.bypassCollector.browserTransport import (
            BrowserTransport,
        )

        return BrowserTransport(task.bypass_config)

    return RequestsTransport(
        headers=task.headers,
        timeout=task.timeout,
    )


def _dedup_pages(
    pages: Iterable[List[Any]],
    dedup_path: str,
) -> Iterator[List[Any]]:
    """
    Drop records whose dedup key has already been seen.

    Fan-out filters (e.g. by nationality) can overlap, so the same record may
    arrive under two variants; this keeps the first and skips the rest. A
    record with no key at ``dedup_path`` is always kept.
    """

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
    hydrate: bool = True,
) -> Iterator[List[Any]]:
    """
    Yield every page of the source.

    Variants partition the dataset so several slices land in one snapshot:
      * ``faceting`` enabled -> slices are computed adaptively from the API's
        reported total (splitting only where a slice exceeds the cap).
      * else ``param_variants`` -> a fixed, hand-declared list of slices.
      * else a single fetch using ``params``, exactly as before.

    ``hydrate`` False yields the raw list stubs without following each record's
    detail endpoint -- used by the list_detail overview phase, which fetches the
    profiles separately.
    """

    if task.faceting.get("enabled"):
        variants = _adaptive_variants(task, transport)
    else:
        variants = task.param_variants or [{}]

    for index, variant in enumerate(variants):
        params = {**task.params, **variant}

        yield from _iter_variant_pages(task, params, transport, hydrate)

        # Space consecutive variant fetches like pages when throttling is on.
        if task.throttle_delay and index < len(variants) - 1:
            time.sleep(task.throttle_delay)


def _make_get_total(task: ApiCollectorTask, transport: Any):
    """
    Build the ``get_total(params)`` used by the planner and the progress hint.

    Asks the list endpoint for a single record and reads the ``total`` it
    reports for the given filters -- one cheap "how many?" query.
    """

    total_path = task.faceting.get("total_path", "total")
    page_param = task.pagination.get("page_param", "page")
    size_param = task.pagination.get("size_param")

    def get_total(facet_params: dict) -> int:
        query = {**task.params, **facet_params, page_param: 1}

        if size_param:
            query[size_param] = 1

        payload = _get_json_with_retry(task, task.url, query, transport)

        total = read_path(payload, total_path) or 0

        # Planning is otherwise silent; show each probe's answer so the phase is
        # visibly progressing and the fan-out decisions are traceable.
        logger.info(
            f"{task.list_name}: total for {facet_params or 'root'} = {total}"
        )

        return total

    return get_total


def plan_source(task: ApiCollectorTask):
    """
    Dry run: compute and return the fan-out plan WITHOUT paging or hydrating.

    Opens the transport and runs only the cheap "how many?" queries the planner
    needs, then stops -- no list pages are read and no profile details are
    fetched. Returns the ``FanoutPlan`` (leaves, unresolved over-cap slices, and
    the root total) so a caller can inspect the cap behaviour for a fraction of
    a full run's cost. Returns None when the source has no faceting enabled
    (nothing to plan -- it fetches directly).
    """

    if not task.faceting.get("enabled"):
        return None

    transport = _build_transport(task)

    with transport:
        return _build_plan(task, transport)


def _build_plan(task: ApiCollectorTask, transport: Any):
    """
    Run the fan-out planner against the live "how many?" endpoint and return the
    full plan. Shared by the real collection path (``_adaptive_variants``) and
    the dry-run ``plan_source`` above, so both compute the plan identically.
    """

    cap = task.faceting.get("cap", 160)
    facets = task.faceting.get("facets", [])

    get_total = _make_get_total(task, transport)

    return plan_fanout(get_total, {}, cap, facets)


def _adaptive_variants(
    task: ApiCollectorTask,
    transport: Any,
) -> List[dict]:
    """
    Compute the fan-out slices from the API's reported total, using the facets
    declared in config. Returns a materialised list so the plan is built up
    front (all "how many?" queries) before any page is fetched.
    """

    plan = _build_plan(task, transport)

    logger.info(
        f"{task.list_name}: adaptive fan-out produced {len(plan.leaves)} "
        f"slices (root total {plan.root_total})"
    )

    # The facets/cap no longer cover the data: some slice stays over the cap
    # even after every facet. Say so loudly, with an example, so a new facet
    # type can be added to faceting.facets.
    if plan.unresolved:
        example = plan.unresolved[0]
        cap = task.faceting.get("cap", 160)
        facets = task.faceting.get("facets", [])

        raise RuntimeError(
            f"{task.list_name}: {len(plan.unresolved)} slice(s) still exceed "
            f"the cap ({cap}) after all {len(facets)} facets -- fetching would "
            f"SILENTLY DROP records. Deepen a name facet (raise max_depth) or "
            f"add a facet type. Example over-cap slice: {example['params']} "
            f"= {example['total']} records."
        )

    return plan.leaves


def _startup_probe(task: ApiCollectorTask, transport: Any) -> int:
    """
    One request that returns the whole-dataset total (for the progress ETA) and
    checks whether the site's real result cap still matches the configured one.

    If the site returns fewer records for a full page than both the configured
    cap and the reported total, the site cap has dropped below our cap -- which
    would silently lose records on slices between the two -- so warn to lower
    ``faceting.cap``. Only runs for faceted sources; returns 0 otherwise.
    """

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
            f"🔴 {task.list_name}: the site returned only {got} records for a "
            f"page of {cap} while {total} exist -- the site cap looks like "
            f"{got}, not {cap}. Lower faceting.cap to {got}."
        )

    return total


def _check_completeness(
    task: ApiCollectorTask,
    written: int,
    total_hint: int,
) -> None:
    """
    Compare what we wrote against the total the API reported, and warn if the
    shortfall is more than a small tolerance.

    A tiny gap is normal (records with no facet value, and the dataset changing
    during a run). A large gap means records were not retrieved -- the facets no
    longer cover the data, or the site cap changed.
    """

    if total_hint <= 0:
        return

    gap = total_hint - written
    tolerance = max(10, int(total_hint * 0.005))

    if gap > tolerance:
        logger.warning(
            f"🔴 {task.list_name}: collected {written} of {total_hint} "
            f"(missing {gap}). Records were not retrieved -- the data may have "
            f"outgrown the facets, or the site cap changed. Add another facet "
            f"to faceting.facets, or lower faceting.cap."
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
    """
    Pass pages through unchanged while logging throughput and an ETA.

    Records are counted as they are yielded (i.e. after their detail has been
    fetched), so the rate reflects real progress. Every ``every`` records it
    logs a line to the terminal; nothing is stored.
    """

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

    logger.info(
        f"done: {count} records in {_format_duration(elapsed)}"
    )


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
    hydrate: bool = True,
) -> Iterator[List[Any]]:
    """
    Yield each page's list of records for one param-set until the API returns
    an empty page. A source declared ``type: "none"`` is not paged: it yields
    a single request's records and stops.

    ``hydrate`` False skips detail hydration and yields the raw list stubs.
    """

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

        # When a detail endpoint is configured, replace each thin list stub
        # with its fully-hydrated record before yielding the page.
        if task.detail and hydrate:
            items = _hydrate_items(task, items, transport)

        yield items

        fetched += page_count

        # A single-request source (``type: "none"``) has no next page: one
        # fetch is the whole dataset, so stop instead of re-requesting it.
        if pagination_type == "none":
            return

        # Capped-API guard against a runaway loop: some endpoints (Interpol)
        # answer an out-of-range page number with a non-empty page instead of an
        # empty one, so "stop on empty page" alone never fires. A capped query
        # yields at most ``cap`` records, and a short page is the last one.
        if no_more_pages(fetched, page_count, cap, page_size):
            return

        page += 1

        if task.throttle_delay:
            time.sleep(task.throttle_delay)


def _detail_url_for(stub: dict, detail_cfg: dict):
    """
    Resolve one stub's detail URL.

    Prefers a ready URL in the stub (``url_path`` -- follows the API's own link,
    avoiding id-formatting quirks); otherwise builds it from an id plus a
    template (``id_path`` + ``url_template``). Returns None when unresolvable.
    """

    url_path = detail_cfg.get("url_path")

    if url_path:
        return read_path(stub, url_path)

    id_value = read_path(stub, detail_cfg["id_path"])

    if not id_value:
        return None

    return build_detail_url(detail_cfg["url_template"], id_value)


def _hydrate_items(
    task: ApiCollectorTask,
    items: List[dict],
    transport: Any,
) -> List[dict]:
    """
    Enrich a page of list stubs with their detail responses.

    Each stub is merged with its detail (nested under ``target_field`` when
    set). When ``detail.concurrency`` > 1 and the transport supports it, the
    page's detail requests run in parallel (a bounded pool) -- the big speed
    win, since detail is one request per record. Otherwise they run one by one.
    Stubs with no resolvable detail URL pass through unchanged.
    """

    detail_cfg = task.detail
    shape = task.record_shape
    target_field = detail_cfg.get("target_field")
    concurrency = int(detail_cfg.get("concurrency", 1))

    urls = []
    positions = []

    for position, stub in enumerate(items):
        url = _detail_url_for(stub, detail_cfg)

        if url:
            urls.append(url)
            positions.append(position)

    if urls:
        if concurrency > 1 and hasattr(transport, "get_json_many"):
            fetched = transport.get_json_many(urls, concurrency)
        else:
            fetched = [
                _get_json_with_retry(task, url, None, transport)
                for url in urls
            ]
    else:
        fetched = []

    detail_by_position = dict(zip(positions, fetched))

    hydrated = list(items)

    for position, stub in enumerate(items):
        detail = detail_by_position.get(position)

        if shape:
            # Structured record: id + list + detail + attachment links.
            hydrated[position] = assemble_record(stub, detail, shape)
        elif detail is not None:
            hydrated[position] = merge_records(stub, detail, target_field)

    if task.throttle_delay:
        time.sleep(task.throttle_delay)

    return hydrated


def _get_page(task: ApiCollectorTask, query: dict, transport: Any) -> Any:
    """
    Fetch and parse one page of JSON, retrying on request errors.
    """

    return _get_json_with_retry(task, task.url, params=query, transport=transport)


def _get_json_with_retry(
    task: ApiCollectorTask,
    url: str,
    params: Any,
    transport: Any,
) -> Any:
    """
    Fetch one JSON document through the transport, retrying on request errors.

    The transport does the actual request (plain ``requests`` or a warm
    browser); this shared loop provides the retry/backoff for both. A
    ``RuntimeError`` (e.g. a 401/403 block) is not retried -- it is raised so
    the failure is loud and immediate.
    """

    for attempt in range(1, task.retry + 1):
        try:
            return transport.get_json(url, params)

        except requests.RequestException:
            if attempt == task.retry:
                raise

    raise RuntimeError(
        f"Failed to collect API page: {url} {params}"
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
) -> int:
    """
    Write every record as one raw JSON line into a single JSONL file.

    Streams page by page, so memory stays flat regardless of dataset size.
    Returns the number of records written.
    """

    written = 0

    with output_path.open("w", encoding="utf-8") as handle:
        for items in pages:
            for item in items:
                handle.write(
                    json.dumps(item, ensure_ascii=False)
                )
                handle.write("\n")
                written += 1

    return written


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
