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
    write_mode: str = "single_jsonl"
    download_dir: Optional[str] = None
    filename: Optional[str] = None
