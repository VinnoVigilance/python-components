import hashlib
import json
from typing import Any

def calculate_file_hash(file_path):

    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(8192), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


def calculate_record_hash(
    canonical_record: dict[str, Any],
) -> str:
    canonical_json = json.dumps(
        canonical_record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    return hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()