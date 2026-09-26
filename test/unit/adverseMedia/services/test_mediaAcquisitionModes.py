import os

from types import SimpleNamespace
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

from services.adverseMediaPipeline.mediaAcquisitionService import (
    MediaAcquisitionService,
)


pytestmark = pytest.mark.unit


def _crawler_config():
    return {
        "source_name": "SRC",
        "dataset_name": "DATA",
        "url": "https://example.test/news",
    }


@pytest.mark.parametrize(
    ("mode", "policy", "expected_stop"),
    [
        ("INITIAL", "stop_after_known", False),
        ("INCREMENTAL", "stop_after_known", True),
        ("INCREMENTAL", "full_scan", False),
    ],
)
def test_mode_and_policy_control_known_record_stop(
    monkeypatch,
    mode,
    policy,
    expected_stop,
):
    crawl_source = MagicMock(
        return_value=SimpleNamespace(records=[])
    )
    monkeypatch.setattr(
        MediaAcquisitionService,
        "_crawl_source",
        crawl_source,
    )

    result = MediaAcquisitionService._acquire_source(
        source_config=_crawler_config(),
        acquisition_type="crawler",
        source_id=1,
        dataset_id=2,
        mode=mode,
        discovery_policy=policy,
        known_threshold=10,
    )

    assert result.records == []
    assert (
        crawl_source.call_args.kwargs[
            "stop_after_known"
        ]
        is expected_stop
    )


@pytest.mark.parametrize(
    ("mode", "policy", "expected_stop"),
    [
        ("INITIAL", "stop_after_known", False),
        ("INCREMENTAL", "stop_after_known", True),
        ("INCREMENTAL", "full_scan", False),
    ],
)
def test_mode_and_policy_control_known_record_stop_for_api(
    monkeypatch,
    mode,
    policy,
    expected_stop,
):
    collect_api_source = MagicMock(
        return_value=SimpleNamespace(records=[])
    )
    monkeypatch.setattr(
        MediaAcquisitionService,
        "_collect_api_source",
        collect_api_source,
    )

    result = MediaAcquisitionService._acquire_source(
        source_config=_crawler_config(),
        acquisition_type="api",
        source_id=1,
        dataset_id=2,
        mode=mode,
        discovery_policy=policy,
        known_threshold=10,
    )

    assert result.records == []
    assert (
        collect_api_source.call_args.kwargs[
            "stop_after_known"
        ]
        is expected_stop
    )
