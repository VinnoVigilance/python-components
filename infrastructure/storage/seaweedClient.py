from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.config import Config

from config.settings import (
    STORAGE_ACCESS_KEY_INGESTION,
    STORAGE_BUCKET_INGESTION,
    STORAGE_REGION,
    STORAGE_S3_ENDPOINT,
    STORAGE_SECRET_KEY_INGESTION,
)


s3_client = boto3.client(
    "s3",
    endpoint_url=STORAGE_S3_ENDPOINT,
    aws_access_key_id=STORAGE_ACCESS_KEY_INGESTION,
    aws_secret_access_key=STORAGE_SECRET_KEY_INGESTION,
    region_name=STORAGE_REGION,
    config=Config(
        signature_version="s3v4",
        s3={
            "addressing_style": "path",
        },
    ),
)


def upload_file(
    file_path: str | Path,
    object_path: str,
) -> str:
    local_file_path = Path(file_path).resolve()

    if not local_file_path.is_file():
        raise FileNotFoundError(
            f"File not found: {local_file_path}"
        )

    s3_client.upload_file(
        Filename=str(local_file_path),
        Bucket=STORAGE_BUCKET_INGESTION,
        Key=object_path,
    )

    return (
        f"s3://{STORAGE_BUCKET_INGESTION}/"
        f"{object_path}"
    )


def download_file(
    storage_path: str,
    destination_path: str | Path,
) -> str:
    """Download one Raw object from SeaweedFS to a local file."""

    parsed_path = urlparse(
        storage_path
    )

    if parsed_path.scheme == "s3":
        bucket = parsed_path.netloc
        object_path = parsed_path.path.lstrip("/")

    elif not parsed_path.scheme:
        bucket = STORAGE_BUCKET_INGESTION
        object_path = storage_path.lstrip("/")

    else:
        raise ValueError(
            "Unsupported Media storage path: "
            f"{storage_path}"
        )

    if not bucket or not object_path:
        raise ValueError(
            "Invalid Media storage path: "
            f"{storage_path}"
        )

    local_path = Path(
        destination_path
    ).resolve()

    local_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    s3_client.download_file(
        Bucket=bucket,
        Key=object_path,
        Filename=str(local_path),
    )

    return str(local_path)
