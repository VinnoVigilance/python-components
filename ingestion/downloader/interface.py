"""Public file-download and manual-file resolution interface."""

from pathlib import Path

from .models import DownloadTask
from .structured_downloader import download_file


def download(task: DownloadTask) -> Path:
    """Download one configured source file."""

    return Path(download_file(task)).resolve()


def resolve_manual_files(
    input_path: str | Path,
    allowed_file_types: list[str],
) -> list[Path]:
    """Return supported files from a manually supplied file or directory."""

    path = Path(input_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Manual input not found: {path}")

    allowed_extensions = {
        extension.lower().lstrip(".") for extension in allowed_file_types
    }
    candidates = (
        [path]
        if path.is_file()
        else [candidate for candidate in path.rglob("*") if candidate.is_file()]
    )
    files = [
        candidate
        for candidate in candidates
        if candidate.suffix.lower().lstrip(".") in allowed_extensions
    ]

    if not files:
        raise ValueError(f"No supported files found in: {path}")

    return sorted(files)
