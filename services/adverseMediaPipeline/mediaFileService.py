import mimetypes
from pathlib import Path
from typing import Any

from utils.hashing import calculate_file_hash


class MediaFileService:
    """
    Builds metadata for an acquired Media file.

    Responsibilities:
    - Validate local file.
    - Determine file metadata.
    - Calculate file_hash using shared project utility.
    """

    @staticmethod
    def build_file_metadata(
        file_path: str,
        file_url: str | None = None,
    ) -> dict[str, Any]:

        path = Path(file_path).resolve()

        if not path.exists():
            raise FileNotFoundError(
                f"Media file does not exist: {path}"
            )

        if not path.is_file():
            raise ValueError(
                f"Media path is not a file: {path}"
            )

        file_name = path.name

        file_type = (
            path.suffix
            .lower()
            .lstrip(".")
        )

        mime_type, _ = mimetypes.guess_type(
            file_name
        )

        file_size = path.stat().st_size

        file_hash = calculate_file_hash(
            path
        )

        return {
            "file_url": file_url,
            "file_name": file_name,
            "file_type": file_type,
            "mime_type": (
                mime_type
                or "application/octet-stream"
            ),
            "file_size": file_size,
            "file_hash": file_hash,
            "local_path": str(path),
        }