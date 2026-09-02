from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ApiCollectorTask:
    """
    Source-agnostic description of a single API acquisition run.

    The service builds this from a watchlist config's ``api_config`` block,
    so the collector code never reads raw config dicts directly.
    """

    url: str
    source_name: str
    list_name: str
    pagination: Dict[str, Any] = field(default_factory=dict)
    items_path: str = "items"
    params: Dict[str, Any] = field(default_factory=dict)
    # Optional: fetch the source once per variant, merging each variant over
    # ``params``, and concatenate the results. Lets one source pull several
    # partitions of the same dataset (e.g. an API split by a ``category`` query
    # param) into a single snapshot. Empty = a single fetch with ``params``.
    param_variants: List[Dict[str, Any]] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    retry: int = 3
    throttle_delay: float = 0.0
    # How collected records are written:
    #   "single_jsonl" one JSONL snapshot (list+detail merged per record)
    #   "list_detail"  CFTC-style split: a primary listing JSONL of unique
    #                  records, plus one raw-profile file per record under
    #                  attachments/members/{id}.json (fetched sequentially)
    write_mode: str = "single_jsonl"
    download_dir: Optional[str] = None
    filename: Optional[str] = None
    # How the JSON is fetched: "requests" (default, open APIs) or "browser"
    # (a warm stealth-browser session, for APIs behind a bot wall).
    transport: str = "requests"
    # Config for the browser transport (warmup_url, headless, ...). Ignored
    # when transport is "requests".
    bypass_config: Dict[str, Any] = field(default_factory=dict)
    # Optional list->detail hydration. When set, each list item is enriched
    # with its own detail response before it is written:
    #   url_path      dot-path to a ready detail URL in the stub (preferred;
    #                 follows the API's own link), OR
    #   id_path       dot-path to the item's id, with
    #   url_template  a detail URL carrying a "{id}" placeholder
    #   target_field  when set, nest the detail object under this key instead
    #                 of flat-merging it into the stub
    #   concurrency   detail requests to run in parallel per page (default 1);
    #                 the main speed lever, since detail is one request/record
    detail: Dict[str, Any] = field(default_factory=dict)
    # Optional adaptive fan-out for capped list APIs that report a total:
    #   enabled     turn it on
    #   cap         max records retrievable per query (e.g. 160)
    #   total_path  dot-path to the total in the list response (default "total")
    #   facets      ordered split rules, e.g.
    #               {"type":"enum","param":"sexId","values":[...]} or
    #               {"type":"range","min_param":"ageMin","max_param":"ageMax",
    #                "low":0,"high":120} or values_ref:"country_codes"
    # When enabled it replaces param_variants with slices computed at runtime.
    faceting: Dict[str, Any] = field(default_factory=dict)
    # Optional structured record shape (id + list + detail + attachment links),
    # matching the crawler's record. See pagination.assemble_record for keys.
    # When set, hydration builds this envelope instead of a flat/nested merge.
    record_shape: Dict[str, Any] = field(default_factory=dict)
    # Optional dot-path used to drop duplicate records across pages/variants
    # (e.g. when fan-out filters overlap). None = keep every record.
    dedup_path: Optional[str] = None
