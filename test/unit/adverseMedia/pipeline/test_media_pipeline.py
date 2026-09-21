"""Wiring test for pipelines.mediaPipeline.run_media_pipeline.

Every service is mocked, so what is under test is the orchestration itself:
each acquired record is processed independently, a failure in one record is
isolated (that record is marked FAILED and the rest still process), an
acquisition failure short-circuits before normalization/core, and unknown or
disabled datasets are rejected up front.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Config-reading modules import at module load; give harmless dummies (a real
# .env value still wins via setdefault). Every real service call is mocked.
os.environ.setdefault("DB_PASSWORD", "test_dummy")
os.environ.setdefault("STORAGE_SECRET_KEY_INGESTION", "test_dummy")
os.environ.setdefault("ELASTICSEARCH_PASSWORD", "test_dummy")

pytest.importorskip("boto3", reason="media pipeline stack (boto3) not installed")

import pipelines.mediaPipeline as mp  # noqa: E402

pytestmark = pytest.mark.unit


def _config(enabled=True):
    return {
        "global": {},
        "sources": {
            "TEST_DATASET": {
                "enabled": enabled,
                "source_name": "TESTSRC",
                "dataset_category": "News",
            }
        },
    }


def _acquisition(records):
    return SimpleNamespace(
        source_id=1,
        dataset_id=2,
        discovered_count=len(records),
        stored_count=len(records),
        duplicate_count=0,
        failed_count=sum(1 for r in records if r.get("failed")),
        records=records,
    )


def _wire(monkeypatch, records, normalize_side_effect=None):
    """Replace every service the orchestrator touches with a mock."""
    monkeypatch.setattr(mp, "load_media_config", lambda: _config())
    monkeypatch.setattr(
        mp.PipelineVersionService, "resolve", MagicMock(return_value="v1")
    )

    acquisition = MagicMock()
    acquisition.acquire.return_value = _acquisition(records)
    monkeypatch.setattr(mp, "MediaAcquisitionService", MagicMock(return_value=acquisition))

    raw = MagicMock()
    raw.process_acquired_record.side_effect = (
        lambda source_config, acquired_record: [{"raw": acquired_record["record_key"]}]
    )
    monkeypatch.setattr(mp, "MediaRawRecordService", MagicMock(return_value=raw))

    normalization = MagicMock()
    if normalize_side_effect is not None:
        normalization.normalize.side_effect = normalize_side_effect
    else:
        normalization.normalize.return_value = {"payload": True}
    monkeypatch.setattr(
        mp, "MediaNormalizationService", MagicMock(return_value=normalization)
    )

    core = MagicMock()
    core.process.return_value = {
        "inserted": True,
        "content_hash": "hash",
        "core_record_id": 99,
    }
    monkeypatch.setattr(mp, "MediaCoreService", MagicMock(return_value=core))

    identity = MagicMock()
    identity.generate_record_key.return_value = "generated-key"
    identity.extract_external_id.return_value = "ext-id"
    monkeypatch.setattr(mp, "MediaIdentityService", identity)

    return SimpleNamespace(
        acquisition=acquisition, raw=raw, normalization=normalization,
        core=core, identity=identity,
    )


class TestMediaPipelineWiring:

    def test_unknown_dataset_raises(self, monkeypatch):
        monkeypatch.setattr(mp, "load_media_config", lambda: _config())
        monkeypatch.setattr(
            mp.PipelineVersionService, "resolve", MagicMock(return_value="v1")
        )
        with pytest.raises(ValueError, match="Unknown Media dataset"):
            mp.run_media_pipeline("NOPE")

    def test_disabled_dataset_raises(self, monkeypatch):
        monkeypatch.setattr(mp, "load_media_config", lambda: _config(enabled=False))
        monkeypatch.setattr(
            mp.PipelineVersionService, "resolve", MagicMock(return_value="v1")
        )
        with pytest.raises(ValueError, match="disabled"):
            mp.run_media_pipeline("TEST_DATASET")

    def test_all_records_processed_and_inserted(self, monkeypatch):
        mocks = _wire(monkeypatch, [
            {"record_key": "a", "media_file_id": 10},
            {"record_key": "b", "media_file_id": 11},
        ])

        result = mp.run_media_pipeline("TEST_DATASET")

        assert result["processed_count"] == 2
        assert result["core_inserted_count"] == 2
        assert result["processing_failed_count"] == 0
        assert result["source_name"] == "TESTSRC"
        assert mocks.core.process.call_count == 2

    def test_one_record_failure_is_isolated(self, monkeypatch):
        # The 2nd record's normalization raises: it is marked FAILED while the
        # 1st and 3rd still insert -- the core reason media records process
        # independently.
        mocks = _wire(
            monkeypatch,
            [
                {"record_key": "a", "media_file_id": 10},
                {"record_key": "b", "media_file_id": 11},
                {"record_key": "c", "media_file_id": 12},
            ],
            normalize_side_effect=[{"ok": 1}, ValueError("boom"), {"ok": 3}],
        )

        result = mp.run_media_pipeline("TEST_DATASET")

        assert result["processed_count"] == 2
        assert result["core_inserted_count"] == 2
        assert result["processing_failed_count"] == 1
        mocks.core.mark_failed.assert_called_once_with(media_file_id=11)

        statuses = {r["record_key"]: r["status"] for r in result["records"]}
        assert statuses == {
            "a": "INSERTED", "b": "PROCESSING_FAILED", "c": "INSERTED",
        }

    def test_acquisition_failed_record_short_circuits(self, monkeypatch):
        mocks = _wire(monkeypatch, [
            {"record_key": "a", "media_file_id": 10},
            {"record_key": "bad", "media_file_id": 11, "failed": True, "error": "download"},
        ])

        result = mp.run_media_pipeline("TEST_DATASET")

        assert result["processed_count"] == 1
        # a failed acquisition never reaches normalization or core
        assert mocks.core.process.call_count == 1
        statuses = {r["record_key"]: r["status"] for r in result["records"]}
        assert statuses["bad"] == "ACQUISITION_FAILED"
