from pathlib import Path
from typing import Any
import mimetypes
from pathlib import Path
from typing import Any
from datetime import datetime


from ingestion.downloader.models import DownloadTask
from utils.hashing import calculate_file_hash
from infrastructure.storage import seaweedClient
from infrastructure.database.connection import connection_pool
from repositories import watchlistFileRepository


ROOT_DIR = Path(__file__).resolve().parents[2]

def calculate_file_metadata(
    file_path: Path,
    original_file_name: str | None = None,
) -> dict[str, Any]:

    source_file_path = Path(file_path).resolve()

    if not source_file_path.exists():
        raise FileNotFoundError(
            f"Source file not found: {source_file_path}"
        )

    if not source_file_path.is_file():
        raise ValueError(
            f"Source path is not a file: {source_file_path}"
        )

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
    """
    Acquire a watchlist source file.

    If `local_path` is defined, the manually provided file is used.
    Otherwise, the source file is downloaded automatically.

    Args:
        config:
            Source-specific watchlist configuration.

        downloader:
            Downloader implementation used for automatic sources.

    Returns:
        Path to the acquired source file.

    Raises:
        FileNotFoundError:
            If the configured manual file does not exist.

        ValueError:
            If neither `local_path` nor `url` is provided.
    """

    local_path = config.get("local_path")

    if local_path:
        return _get_manual_file(
            local_path=local_path,
        )

    source_url = config.get("url")

    if not source_url:
        raise ValueError(
            "The watchlist configuration must contain "
            "either 'local_path' or 'url'."
        )

    return _download_source_file(
        config=config,
        downloader=downloader,
    )


def _get_manual_file(
    local_path: str,
) -> Path:
    """
    Resolve and validate a manually provided source file.
    """

    source_file_path = Path(local_path)

    if not source_file_path.is_absolute():
        source_file_path = ROOT_DIR / source_file_path

    source_file_path = source_file_path.resolve()

    if not source_file_path.exists():
        raise FileNotFoundError(
            f"Manual source file not found: "
            f"{source_file_path}"
        )

    if not source_file_path.is_file():
        raise FileNotFoundError(
            f"Manual source path is not a file: "
            f"{source_file_path}"
        )

    return source_file_path


def _download_source_file(
    config: dict[str, Any],
    downloader: Any,
) -> Path:
    """
    Download a source file using the configured downloader.
    """

    source_name = config["source_name"]

    list_name = config.get(
        "list_name",
        source_name,
    )

    download_task = DownloadTask(
    url=config["url"],
    source_name=config["source_name"],
    list_name=config.get(
        "list_name",
        config["source_name"],
    ),
    file_type=config.get("file_type"),
)

    downloaded_file_path = downloader.download(
        download_task
    )

    source_file_path = Path(
        downloaded_file_path
    ).resolve()

    if not source_file_path.exists():
        raise FileNotFoundError(
            f"Downloaded source file was not found: "
            f"{source_file_path}"
        )

    return source_file_path

def store_source_file(
    config: dict[str, Any],
    file_path: str | Path,
) -> str:
    source_file_path = Path(file_path)
    stored_at = datetime.now()

    source_name = config["source_name"]
    list_name = config.get(
        "list_name",
        source_name,
    )

    object_path = (
        f"{source_name}/"
        f"{list_name}/"
        f"year={stored_at:%Y}/"
        f"month={stored_at:%m}/"
        f"day={stored_at:%d}/"
        f"{source_file_path.name}"
    )

    return seaweedClient.upload_file(
        file_path=source_file_path,
        object_path=object_path,
    )

def resolve_lookup_values(
    config: dict[str, Any],
) -> dict[str, int]:
    source_name = config["source_name"]

    list_name = config.get(
        "list_name",
        source_name,
    )

    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                source_id = (
                    watchlistFileRepository.find_source_id(
                        cursor=cursor,
                        source_name=source_name,
                    )
                )

                if source_id is None:
                    raise LookupError(
                        f"Source not found: {source_name}"
                    )

                list_type_id = (
                    watchlistFileRepository.find_list_type_id(
                        cursor=cursor,
                        source_id=source_id,
                        list_name=list_name,
                    )
                )

                if list_type_id is None:
                    raise LookupError(
                        f"List type not found: {list_name} "
                        f"for source: {source_name}"
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
) -> str:
    connection = connection_pool.getconn()

    try:
        with connection:
            with connection.cursor() as cursor:
                existing_file = (
                    watchlistFileRepository.find_latest_file(
                        cursor=cursor,
                        source_id=source_id,
                        list_type_id=list_type_id,
                    )
                )

    finally:
        connection_pool.putconn(connection)

    if existing_file is None:
        return "FIRST_DOWNLOAD"

    if existing_file["file_hash"] == file_hash:
        return "DUPLICATE"

    return "NEW_VERSION"