import os

from unittest.mock import MagicMock

import pytest


os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault(
    "STORAGE_SECRET_KEY_INGESTION",
    "test_dummy",
)

pytest.importorskip(
    "boto3",
    reason="Media storage stack is not installed.",
)

from services.adverseMediaPipeline import (  # noqa: E402
    mediaReprocessingService as module,
)


pytestmark = pytest.mark.unit


def _raw_file(file_id, record_key):
    return {
        "id": file_id,
        "record_key": record_key,
        "file_url": f"https://example.test/{file_id}",
        "file_name": f"{file_id}.json",
        "file_type": "json",
        "storage_path": f"s3://raw/{file_id}.json",
        "file_hash": f"hash-{file_id}",
        "status": "PARSED",
    }


def test_reprocess_loads_current_raw_files_without_source_acquisition(
    monkeypatch,
):
    raw_files = [
        _raw_file(10, "SRC|DATA|10"),
        _raw_file(11, "SRC|DATA|11"),
    ]

    monkeypatch.setattr(
        module.MediaReprocessingService,
        "_find_raw_files",
        MagicMock(return_value=(1, 2, raw_files)),
    )

    download = MagicMock(
        side_effect=lambda storage_path, destination_path: str(
            destination_path
        )
    )
    monkeypatch.setattr(
        module.seaweedClient,
        "download_file",
        download,
    )

    raw_service = MagicMock()
    raw_service.extract.side_effect = [
        [{"id": 10}],
        [{"id": 11}],
    ]
    monkeypatch.setattr(
        module,
        "MediaRawRecordService",
        MagicMock(return_value=raw_service),
    )

    result = module.MediaReprocessingService().load(
        source_config={
            "source_name": "SRC",
            "dataset_name": "DATA",
        }
    )

    assert result.source_id == 1
    assert result.dataset_id == 2
    assert result.discovered_count == 2
    assert result.failed_count == 0
    assert [
        record["record_key"]
        for record in result.records
    ] == [
        "SRC|DATA|10",
        "SRC|DATA|11",
    ]
    assert download.call_count == 2
    assert raw_service.extract.call_count == 2


def test_reprocess_isolates_one_raw_file_failure(
    monkeypatch,
):
    raw_files = [
        _raw_file(10, "SRC|DATA|10"),
        _raw_file(11, "SRC|DATA|11"),
    ]

    monkeypatch.setattr(
        module.MediaReprocessingService,
        "_find_raw_files",
        MagicMock(return_value=(1, 2, raw_files)),
    )

    download = MagicMock(
        side_effect=[
            "/tmp/10.json",
            RuntimeError("storage unavailable"),
        ]
    )
    monkeypatch.setattr(
        module.seaweedClient,
        "download_file",
        download,
    )

    raw_service = MagicMock()
    raw_service.extract.return_value = [
        {"id": 10}
    ]
    monkeypatch.setattr(
        module,
        "MediaRawRecordService",
        MagicMock(return_value=raw_service),
    )

    result = module.MediaReprocessingService().load(
        source_config={
            "source_name": "SRC",
            "dataset_name": "DATA",
        }
    )

    assert result.failed_count == 1
    assert result.records[0]["failed"] is False
    assert result.records[1]["failed"] is True
    assert "storage unavailable" in result.records[1][
        "error"
    ]
