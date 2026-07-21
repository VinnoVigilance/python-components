import hashlib
from datetime import datetime
from pathlib import Path

import requests

from .models import DownloadTask


ROOT_DIR = Path(__file__).resolve().parents[2]
DOWNLOAD_ROOT = ROOT_DIR / "data" / "downloads"


def _generate_filename(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _get_original_filename(task: DownloadTask) -> str:
    if task.filename:
        return task.filename

    name = task.url.split("/")[-1]

    if name:
        return name

    return _generate_filename(task.url)


def _build_download_directory(
    task: DownloadTask,
    downloaded_at: datetime,
) -> Path:
    download_root = (
        Path(task.download_dir)
        if task.download_dir
        else DOWNLOAD_ROOT
    )

    return (
        download_root
        / task.source_name
        / task.list_name
        / f"year={downloaded_at:%Y}"
        / f"month={downloaded_at:%m}"
        / f"day={downloaded_at:%d}"
    )


def _build_final_filename(
    task: DownloadTask,
    original_name: str,
    downloaded_at: datetime,
) -> str:
    timestamp = downloaded_at.strftime("%Y%m%d_%H%M%S")
    extension = Path(original_name).suffix

    return f"{task.list_name}_{timestamp}{extension}"


def download_file(task: DownloadTask) -> str:
    downloaded_at = datetime.now()

    original_name = _get_original_filename(task)

    download_directory = _build_download_directory(
        task=task,
        downloaded_at=downloaded_at,
    )

    download_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    final_filename = _build_final_filename(
        task=task,
        original_name=original_name,
        downloaded_at=downloaded_at,
    )

    file_path = download_directory / final_filename

    headers = task.headers or {
        "User-Agent": "Mozilla/5.0",
    }

    for attempt in range(1, task.retry + 1):
        try:
            response = requests.get(
                task.url,
                headers=headers,
                timeout=task.timeout,
                stream=True,
                allow_redirects=True,
            )

            response.raise_for_status()

            with file_path.open("wb") as file:
                for chunk in response.iter_content(
                    chunk_size=8192,
                ):
                    if chunk:
                        file.write(chunk)

            return str(file_path)

        except requests.RequestException:
            if attempt == task.retry:
                raise

    raise RuntimeError(
        f"Failed to download source file: {task.url}"
    )