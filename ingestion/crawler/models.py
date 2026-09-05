from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CrawlerTask:
    """
    Source-agnostic description of a crawler acquisition run.
    """

    url: str
    source_name: str
    list_name: str
    source_config_path: str

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