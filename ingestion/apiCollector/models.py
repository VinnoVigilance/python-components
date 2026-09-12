from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ApiCollectorTask:
    """Source-agnostic description of a single API acquisition run."""

    url: str
    source_name: str
    list_name: str
    pagination: Dict[str, Any] = field(default_factory=dict)
    items_path: str = "items"
    params: Dict[str, Any] = field(default_factory=dict)
    param_variants: List[Dict[str, Any]] = field(default_factory=list)
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    retry: int = 3
    throttle_delay: float = 0.0
    write_mode: str = "single_jsonl"
    download_dir: Optional[str] = None
    filename: Optional[str] = None
    transport: str = "requests"
    bypass_config: Dict[str, Any] = field(default_factory=dict)
    detail: Dict[str, Any] = field(default_factory=dict)
    record_shape: Dict[str, Any] = field(default_factory=dict)
    dedup_path: Optional[str] = None
    faceting: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "ApiCollectorTask":
        """Build a task from a watchlist config's ``api_config`` block."""

        api_config = config.get("api_config", {})

        return cls(
            url=config["url"],
            source_name=config["source_name"],
            list_name=config.get("list_name", config["source_name"]),
            pagination=api_config.get("pagination", {}),
            items_path=api_config.get("items_path", "items"),
            params=api_config.get("params", {}),
            param_variants=api_config.get("param_variants", []),
            headers=api_config.get("headers", {}),
            timeout=api_config.get("timeout", 30),
            retry=api_config.get("retry", 3),
            throttle_delay=api_config.get("throttle_delay", 0.0),
            write_mode=api_config.get("write_mode", "single_jsonl"),
            transport=api_config.get("transport", "requests"),
            bypass_config=api_config.get("bypass_config", {}),
            detail=api_config.get("detail", {}),
            faceting=api_config.get("faceting", {}),
            record_shape=api_config.get("record_shape", {}),
            dedup_path=api_config.get("dedup_path"),
        )
