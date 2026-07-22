from pathlib import Path

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