from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CrawlerTask:
    """
    Source-agnostic description of a crawler acquisition run.

    Watchlist usually uses source_config_path.

    Adverse Media can pass an already-loaded
    source_config directly.
    """

    url: str
    source_name: str
    list_name: str

    # Existing Watchlist config path.
    # Optional now because Media can pass source_config directly.
    source_config_path: Optional[str] = None

    # Used by Adverse Media or any caller that already
    # has the crawler/source config loaded.
    source_config: Optional[Dict[str, Any]] = None

    # Existing behavior is preserved.
    fetch_strategy: str = "direct"

    headers: Dict[str, str] = field(
        default_factory=dict
    )

    timeout: int = 30
    retry: int = 3

    download_dir: Optional[str] = None
    source_file_path: Optional[str] = None


@dataclass
class CrawlResult:
    """
    Result of a crawler acquisition run.

    HTML files may be stored on disk, but extracted
    records are returned directly in memory.
    """

    source_file_path: Optional[str]
    records: list[dict[str, Any]]
    record_count: int