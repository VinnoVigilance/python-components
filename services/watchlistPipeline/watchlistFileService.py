import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from infrastructure.database.connection import connection_pool
from infrastructure.storage import seaweedClient
from ingestion.apiCollector.interface import ApiCollectorTask, collect
from ingestion.crawler.interface import crawl
from ingestion.crawler.models import CrawlerTask
from ingestion.downloader.models import DownloadTask
from ingestion.bypassCollector import BypassCollector
from repositories import jobStateRepository
from repositories import watchlistFileLogRepository
from repositories import watchlistFileRepository
from utils.hashing import calculate_file_hash


ROOT_DIR = Path(__file__).resolve().parents[2]
WATCHLIST_HOLD_KIND = "watchlist"


@dataclass
class AcquisitionResult:
    source_file_path: Path
    records: list[dict[str, Any]] | None = None
    broken_details: list[dict[str, Any]] = field(default_factory=list)


def acquire_source(config: dict[str, Any], downloader: Any) -> AcquisitionResult:
    """Acquire a source and return a consistent acquisition result."""

    if str(config.get("download_method", "")).upper() == "CRAWLER":
        source_config_path = config.get("source_config")

        if not source_config_path:
            raise ValueError("Crawler source must define 'source_config'.")

        task = CrawlerTask(
            url=config["url"],
            source_name=config["source_name"],
            list_name=config.get("list_name", config["source_name"]),
            source_config_path=source_config_path,
            download_dir=str(ROOT_DIR / "data" / "downloads"),
        )

        crawl_result = crawl(task)

        if not crawl_result.source_file_path:
            raise FileNotFoundError("Crawler did not produce a source HTML file.")

        source_file_path = Path(crawl_result.source_file_path).resolve()

        if not source_file_path.exists():
            raise FileNotFoundError(
                f"Crawler source file was not found: {source_file_path}"
            )

        broken = hold_incomplete_crawl(config, crawl_result, source_file_path)

        return AcquisitionResult(
            source_file_path=source_file_path,
            records=crawl_result.records,
            broken_details=broken,
        )

    source_file_path = acquire_source_file(
        config=config,
        downloader=downloader,
    )

    return AcquisitionResult(source_file_path=source_file_path)


class CrawlOnHold(Exception):
    """A crawl missed detail pages, so its list waits in a holding file."""

    def __init__(self, watchlist_name: str, expected_count: int, missing: list[dict]):
        self.watchlist_name = watchlist_name
        self.expected_count = expected_count
        self.missing = missing
        self.missing_urls = [item["detail_url"] for item in missing]
        super().__init__(
            f"{watchlist_name} is waiting: read {expected_count - len(missing)} of "
            f"{expected_count} detail pages. Missing: {', '.join(self.missing_urls[:10])}"
        )


def _broken_on_source(missing: list[dict]) -> list[dict]:
    return [item for item in missing if item.get("permanent")]


def hold_incomplete_crawl(
    config: dict[str, Any],
    crawl_result: Any,
    source_file_path: Path,
) -> list[dict]:
    """Hold a crawl that missed pages for temporary reasons; return pages broken on the source."""

    watchlist_name = config["list_name"]
    broken = _broken_on_source(crawl_result.missing_details)

    if len(broken) == crawl_result.missing_detail_count:
        jobStateRepository.delete_state(WATCHLIST_HOLD_KIND, watchlist_name)
        return broken

    now = datetime.now(timezone.utc).isoformat()
    jobStateRepository.save_state(
        WATCHLIST_HOLD_KIND,
        watchlist_name,
        {
            "watchlist_name": watchlist_name,
            "source_file_path": str(source_file_path),
            "held_at": now,
            "last_tried_at": now,
            "attempts": 1,
            "expected_count": crawl_result.selected_detail_count,
            "records": crawl_result.records,
            "missing": crawl_result.missing_details,
        },
    )

    raise CrawlOnHold(
        watchlist_name,
        crawl_result.selected_detail_count,
        crawl_result.missing_details,
    )


def retry_held_crawl(config: dict[str, Any]) -> AcquisitionResult | None:
    """Fetch a held list's missing detail pages; return the full list once complete."""

    watchlist_name = config["list_name"]
    held = jobStateRepository.load_state(WATCHLIST_HOLD_KIND, watchlist_name)

    if held is None:
        return None

    crawl_result = crawl(
        CrawlerTask(
            url=config["url"],
            source_name=config["source_name"],
            list_name=watchlist_name,
            source_config_path=str((ROOT_DIR / config["source_config"]).resolve()),
            source_file_path=held["source_file_path"],
            download_dir=str(ROOT_DIR / "data" / "downloads"),
            detail_items=held["missing"],
        )
    )

    held.update(
        attempts=held["attempts"] + 1,
        last_tried_at=datetime.now(timezone.utc).isoformat(),
        records=held["records"] + crawl_result.records,
        missing=crawl_result.missing_details,
    )

    broken = _broken_on_source(crawl_result.missing_details)

    if len(broken) < crawl_result.missing_detail_count:
        jobStateRepository.save_state(WATCHLIST_HOLD_KIND, watchlist_name, held)
        raise CrawlOnHold(watchlist_name, held["expected_count"], held["missing"])

    return AcquisitionResult(
        source_file_path=Path(held["source_file_path"]),
        records=held["records"],
        broken_details=broken,
    )


def release_held_crawl(config: dict[str, Any]) -> None:
    jobStateRepository.delete_state(WATCHLIST_HOLD_KIND, config["list_name"])


def calculate_file_metadata(
    file_path: Path,
    original_file_name: str | None = None,
) -> dict[str, Any]:
    source_file_path = Path(file_path).resolve()

    if not source_file_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_file_path}")

    if not source_file_path.is_file():
        raise ValueError(f"Source path is not a file: {source_file_path}")

    file_name = original_file_name or source_file_path.name
    file_type = Path(file_name).suffix.lstrip(".").lower()
    mime_type, _ = mimetypes.guess_type(file_name)

    return {
        "file_name": file_name,
        "file_size": source_file_path.stat().st_size,
        "mime_type": mime_type or "application/octet-stream",
        "file_type": file_type,
        "file_hash": calculate_file_hash(source_file_path),
    }


def acquire_source_file(
    config: dict[str, Any],
    downloader: Any,
) -> Path:
    """Acquire a normal file/API/manual watchlist source."""

    if config.get("download_method") == "API":
        return _collect_api_source(config=config)

    if config.get("download_method") == "BYPASS":
        return _collect_bypass_source(config=config)

    local_path = config.get("local_path")

    if local_path:
        return _get_manual_file(local_path=local_path)

    source_url = config.get("url")

    if not source_url:
        raise ValueError(
            "The watchlist configuration must contain either 'local_path' or 'url'."
        )

    return _download_source_file(
        config=config,
        downloader=downloader,
    )


def _get_manual_file(local_path: str) -> Path:
    """Resolve and validate a manually provided source file."""

    source_file_path = Path(local_path)

    if not source_file_path.is_absolute():
        source_file_path = ROOT_DIR / source_file_path

    source_file_path = source_file_path.resolve()

    if not source_file_path.exists():
        raise FileNotFoundError(f"Manual source file not found: {source_file_path}")

    if not source_file_path.is_file():
        raise FileNotFoundError(
            f"Manual source path is not a file: {source_file_path}"
        )

    return source_file_path


def _collect_api_source(config: dict[str, Any]) -> Path:
    """Acquire an API-based source into a JSONL snapshot."""

    collected_path = collect(ApiCollectorTask.from_config(config))
    source_file_path = Path(collected_path).resolve()

    if not source_file_path.exists():
        raise FileNotFoundError(
            f"Collected API snapshot was not found: {source_file_path}"
        )

    return source_file_path


def _collect_bypass_source(
    config: dict[str, Any],
) -> Path:
    """
    Acquire a bot-protected source by driving a stealth browser.

    The BypassCollector navigates the page, clears any anti-bot challenge
    (e.g. Cloudflare), runs the configured actions, and saves the rendered
    HTML artifact. It reads its ``bypass_config`` block off the config dict.
    """

    collected_path = BypassCollector().collect(config)

    if collected_path is None:
        raise RuntimeError(
            f"Bypass collection failed for "
            f"{config.get('source_name')}/"
            f"{config.get('list_name')}."
        )

    source_file_path = Path(collected_path).resolve()

    if not source_file_path.exists():
        raise FileNotFoundError(
            f"Collected bypass artifact was not found: "
            f"{source_file_path}"
        )

    return source_file_path


def _download_source_file(
    config: dict[str, Any],
    downloader: Any,
) -> Path:
    """Download a source file using the configured downloader."""

    download_task = DownloadTask(
        url=config["url"],
        source_name=config["source_name"],
        list_name=config.get("list_name", config["source_name"]),
        file_type=config.get("file_type"),
    )

    downloaded_file_path = downloader.download(download_task)
    source_file_path = Path(downloaded_file_path).resolve()

    if not source_file_path.exists():
        raise FileNotFoundError(
            f"Downloaded source file was not found: {source_file_path}"
        )

    return source_file_path


def store_source_file(
    config: dict[str, Any],
    file_path: str | Path,
) -> str:
    source_file_path = Path(file_path)
    stored_at = datetime.now()

    source_name = config["source_name"]
    list_name = config.get("list_name", source_name)

    object_path = (
        f"{source_name}/{list_name}/"
        f"year={stored_at:%Y}/month={stored_at:%m}/day={stored_at:%d}/"
        f"{source_file_path.name}"
    )

    return seaweedClient.upload_file(
        file_path=source_file_path,
        object_path=object_path,
    )


def resolve_lookup_values(config: dict[str, Any]) -> dict[str, int]:
    source_name = config["source_name"]
    list_name = config.get("list_name", source_name)

    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                source_id = watchlistFileRepository.find_source_id(
                    cursor=cursor,
                    source_name=source_name,
                )

                if source_id is None:
                    raise LookupError(f"Source not found: {source_name}")

                list_type_id = watchlistFileRepository.find_list_type_id(
                    cursor=cursor,
                    source_id=source_id,
                    list_name=list_name,
                )

                if list_type_id is None:
                    raise LookupError(
                        f"List type not found: {list_name} for source: {source_name}"
                    )

                return {
                    "source_id": source_id,
                    "list_type_id": list_type_id,
                }

    finally:
        connection_pool.putconn(connection)


def check_duplicate(
    source_id: int,
    list_type_id: int,
    file_hash: str,
) -> dict[str, Any]:
    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                existing_file = watchlistFileRepository.find_latest_file(
                    cursor=cursor,
                    source_id=source_id,
                    list_type_id=list_type_id,
                )
    finally:
        connection_pool.putconn(connection)

    if existing_file is None:
        return {"duplicate_status": "FIRST_DOWNLOAD"}

    if existing_file["file_hash"] != file_hash:
        return {"duplicate_status": "NEW_VERSION"}

    result = {
        "watchlist_file_id": existing_file["id"],
        "file_version": existing_file["file_version"],
        "storage_path": existing_file["storage_path"],
        "file_status": existing_file["status"],
    }

    if existing_file["normalization_completed"]:
        result["duplicate_status"] = "DUPLICATE_COMPLETED"

    elif existing_file["has_raw_payloads"]:
        result["duplicate_status"] = "RESUME_NORMALIZATION"

    else:
        result["duplicate_status"] = "RESUME_PROCESSING"

    return result


def determine_file_version(
    config: dict[str, Any],
    duplicate_status: str,
    source_id: int,
    list_type_id: int,
) -> str | None:
    versioning_strategy = config["versioning_strategy"]

    if versioning_strategy == "independent":
        return None

    if duplicate_status == "DUPLICATE":
        raise ValueError("Duplicate files must not receive a new version.")

    if duplicate_status == "FIRST_DOWNLOAD":
        return "1"

    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                latest_version = watchlistFileRepository.find_latest_file_version(
                    cursor=cursor,
                    source_id=source_id,
                    list_type_id=list_type_id,
                )
    finally:
        connection_pool.putconn(connection)

    if latest_version is None:
        raise ValueError("Latest file version was not found.")

    try:
        return str(int(latest_version) + 1)

    except ValueError as error:
        raise ValueError(
            f"Invalid latest file version: {latest_version}"
        ) from error


def insert_watchlist_file(
    config: dict[str, Any],
    file_metadata: dict[str, Any],
    source_id: int,
    list_type_id: int,
    storage_path: str,
    file_version: str | None,
) -> int:
    file_data = {
        "source_id": source_id,
        "list_type_id": list_type_id,
        "list_url": config.get("url"),
        "storage_path": storage_path,
        "file_name": file_metadata["file_name"],
        "file_type": file_metadata["file_type"],
        "mime_type": file_metadata["mime_type"],
        "file_size": file_metadata["file_size"],
        "file_hash": file_metadata["file_hash"],
        "file_version": file_version,
        "download_method": config["download_method"],
    }

    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                watchlist_file_id = watchlistFileRepository.insert_watchlist_file(
                    cursor=cursor,
                    file_data=file_data,
                )

                watchlistFileLogRepository.insert_file_log(
                    cursor=cursor,
                    file_id=watchlist_file_id,
                    step="DOWNLOAD",
                    status="SUCCESS",
                    message="Source file downloaded and registered successfully.",
                )

        return watchlist_file_id

    finally:
        connection_pool.putconn(connection)


def mark_watchlist_file_as_failed(
    watchlist_file_id: int,
    step: str,
    error: Exception,
    duration_ms: int | None = None,
) -> None:
    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                watchlistFileRepository.mark_file_as_failed(
                    cursor=cursor,
                    watchlist_file_id=watchlist_file_id,
                )

                watchlistFileLogRepository.insert_file_log(
                    cursor=cursor,
                    file_id=watchlist_file_id,
                    step=step,
                    status="FAILED",
                    message=f"{step} step failed.",
                    error_code=type(error).__name__,
                    error_details=str(error),
                    duration_ms=duration_ms,
                )

    finally:
        connection_pool.putconn(connection)


def insert_file_log(
    file_id: int,
    step: str,
    status: str,
    message: str | None = None,
    error_code: str | None = None,
    error_details: str | None = None,
    duration_ms: int | None = None,
) -> int:
    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                return watchlistFileLogRepository.insert_file_log(
                    cursor=cursor,
                    file_id=file_id,
                    step=step,
                    status=status,
                    message=message,
                    error_code=error_code,
                    error_details=error_details,
                    duration_ms=duration_ms,
                )

    finally:
        connection_pool.putconn(connection)