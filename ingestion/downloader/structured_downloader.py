import hashlib
import logging
from time import perf_counter
from datetime import datetime
from pathlib import Path

import requests
from urllib.parse import urlparse

from .models import DownloadTask


ROOT_DIR = Path(__file__).resolve().parents[2]
DOWNLOAD_ROOT = ROOT_DIR / "data" / "downloads"
logger = logging.getLogger(__name__)


def _generate_filename(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


# TO
def _get_original_filename(task: DownloadTask) -> str:
    if task.filename:
        return task.filename

    name = urlparse(task.url).path.split("/")[-1]

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
    host = urlparse(task.url).hostname or "unknown"

    for attempt in range(1, task.retry + 1):
        started = perf_counter()

        logger.info(
            "HTTP_DOWNLOAD_STARTED "
            "host=%s request=%d/%d timeout=%ss",
            host,
            attempt,
            task.retry,
            task.timeout,
            extra={
                "event": "HTTP_DOWNLOAD_STARTED",
                "stage": "DOWNLOAD",
                "job_data": {
                    "host": host,
                    "http_attempt": attempt,
                    "http_max_attempts": task.retry,
                    "timeout_seconds": task.timeout,
                },
            },
        )

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

            downloaded_bytes = file_path.stat().st_size
            duration = round(
                perf_counter() - started,
                2,
            )

            logger.info(
                "HTTP_DOWNLOAD_FINISHED "
                "host=%s request=%d/%d status=%d "
                "bytes=%d duration=%.2fs",
                host,
                attempt,
                task.retry,
                response.status_code,
                downloaded_bytes,
                duration,
                extra={
                    "event": "HTTP_DOWNLOAD_FINISHED",
                    "stage": "DOWNLOAD",
                    "job_data": {
                        "host": host,
                        "http_attempt": attempt,
                        "http_max_attempts": task.retry,
                        "http_status": response.status_code,
                        "file_size_bytes": downloaded_bytes,
                        "duration_seconds": duration,
                    },
                },
            )

            return str(file_path)

        except requests.RequestException as error:
            duration = round(
                perf_counter() - started,
                2,
            )
            http_status = getattr(
                getattr(error, "response", None),
                "status_code",
                None,
            )

            logger.warning(
                "HTTP_DOWNLOAD_FAILED "
                "host=%s request=%d/%d "
                "duration=%.2fs http_status=%s "
                "error_type=%s error=%s",
                host,
                attempt,
                task.retry,
                duration,
                http_status,
                type(error).__name__,
                error,
                extra={
                    "event": "HTTP_DOWNLOAD_FAILED",
                    "stage": "DOWNLOAD",
                    "job_data": {
                        "host": host,
                        "http_attempt": attempt,
                        "http_max_attempts": task.retry,
                        "duration_seconds": duration,
                        "http_status": http_status,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                },
            )

            if attempt == task.retry:
                raise

    raise RuntimeError(
        f"Failed to download source file: {task.url}"
    )