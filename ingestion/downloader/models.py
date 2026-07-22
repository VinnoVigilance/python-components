from dataclasses import dataclass, field
from typing import Optional, Dict

@dataclass
class DownloadTask:
    url: str
    source_name: str
    list_name: str
    download_dir: Optional[str] = None
    filename: Optional[str] = None
    file_type: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: int = 30
    retry: int = 3