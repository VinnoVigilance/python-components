import os

from unittest.mock import (
    MagicMock,
)

import pytest


pytest.importorskip(
    "boto3",
    reason=(
        "Full pipeline stack "
        "(boto3) not installed"
    ),
)

os.environ.setdefault(
    "DB_PASSWORD",
    "test_dummy",
)

os.environ.setdefault(
    "STORAGE_SECRET_KEY_INGESTION",
    "test_dummy",
)


from services.adverseMediaPipeline import (  # noqa: E402
    mediaAcquisitionService as module,
)


pytestmark = pytest.mark.unit


def _mock_database(
    monkeypatch,
):
    connection = MagicMock()
    cursor = MagicMock()

    connection.cursor.return_value.__enter__.return_value = (
        cursor
    )

    connection_pool = MagicMock()

    connection_pool.getconn.return_value = (
        connection
    )

    monkeypatch.setattr(
        module,
        "connection_pool",
        connection_pool,
    )

    discovery_service = MagicMock()

    discovery_service_class = MagicMock(
        return_value=discovery_service
    )

    monkeypatch.setattr(
        module,
        "MediaDiscoveryService",
        discovery_service_class,
    )

    return (
        connection,
        connection_pool,
        discovery_service,
        discovery_service_class,
    )


def _amlc_style_config():
    """No discovery.stop_condition -- the current AMLC shape."""

    return {
        "source_name": "AMLC",
        "dataset_name": "AMLC_NEWS_AND_ANNOUNCEMENTS",
        "url": "https://www.amlc.gov.ph/api/news/combined/feed",
        "acquisition": {
            "type": "api",
            "download_method": "API",
        },
        "api_config": {
            "write_mode": "record_files",
            "record_id_path": "id",
        },
        "identity": {
            "record_key": {
                "fields": [
                    "source_name",
                    "dataset_name",
                    "content_id",
                ],
            },
        },
    }


def _ukgov_style_config():
    """discovery.stop_condition configured -- the UK_GOV shape."""

    config = _amlc_style_config()

    config["source_name"] = "UK_GOV"
    config["dataset_name"] = "UK_GOV_NEWS_COMMUNICATIONS"

    config["discovery"] = {
        "stop_condition": {
            "type": "consecutive_known_records",
            "threshold": 10,
        },
    }

    return config


class TestCollectApiSourceWithoutStopCondition:
    """AMLC-shaped config: no discovery.stop_condition -> unchanged behaviour."""

    def test_does_not_touch_the_database(
        self,
        monkeypatch,
    ):
        (
            connection,
            connection_pool,
            discovery_service,
            discovery_service_class,
        ) = _mock_database(
            monkeypatch
        )

        collection_result = MagicMock()
        collection_result.file_paths = [
            "a.json",
            "b.json",
        ]

        collect_artifacts_mock = MagicMock(
            return_value=collection_result
        )

        monkeypatch.setattr(
            module,
            "collect_artifacts",
            collect_artifacts_mock,
        )

        result = (
            module.MediaAcquisitionService
            ._collect_api_source(
                source_config=_amlc_style_config(),
                source_id=1,
                dataset_id=2,
                known_threshold=10,
            )
        )

        assert result == [
            {"detail_file_path": "a.json"},
            {"detail_file_path": "b.json"},
        ]

        connection_pool.getconn.assert_not_called()
        discovery_service_class.assert_not_called()

        collect_artifacts_mock.assert_called_once()

        # No stop_check kwarg at all -- collect_artifacts must run exactly
        # as it did before this feature existed.
        assert "stop_check" not in (
            collect_artifacts_mock.call_args.kwargs
        )


class TestCollectApiSourceWithStopCondition:
    """UK_GOV-shaped config: discovery.stop_condition -> wires the checker."""

    def test_builds_discovery_service_and_passes_stop_check(
        self,
        monkeypatch,
    ):
        (
            connection,
            connection_pool,
            discovery_service,
            discovery_service_class,
        ) = _mock_database(
            monkeypatch
        )

        collection_result = MagicMock()
        collection_result.file_paths = [
            "a.json",
        ]

        collect_artifacts_mock = MagicMock(
            return_value=collection_result
        )

        monkeypatch.setattr(
            module,
            "collect_artifacts",
            collect_artifacts_mock,
        )

        result = (
            module.MediaAcquisitionService
            ._collect_api_source(
                source_config=_ukgov_style_config(),
                source_id=5,
                dataset_id=6,
                known_threshold=10,
            )
        )

        assert result == [
            {"detail_file_path": "a.json"},
        ]

        discovery_service_class.assert_called_once_with(
            cursor=connection.cursor.return_value.__enter__.return_value,
            source_id=5,
            dataset_id=6,
            source_config=_ukgov_style_config(),
            known_threshold=10,
        )

        collect_artifacts_mock.assert_called_once()

        assert callable(
            collect_artifacts_mock.call_args.kwargs[
                "stop_check"
            ]
        )

        connection_pool.putconn.assert_called_once_with(
            connection
        )


class TestBuildApiStopCheck:
    """Direct unit tests of the consecutive-known counting callback."""

    def _discovery_service_returning(
        self,
        known_flags,
    ):
        """A fake MediaDiscoveryService.check_record_key sequence."""

        discovery_service = MagicMock()

        consecutive = {
            "count": 0,
        }

        def check_record_key(
            record_key,
        ):
            is_known = known_flags[
                check_record_key.calls
            ]

            check_record_key.calls += 1

            if is_known:
                consecutive["count"] += 1
            else:
                consecutive["count"] = 0

            return (
                is_known,
                consecutive["count"] >= 10,
            )

        check_record_key.calls = 0

        discovery_service.check_record_key.side_effect = (
            check_record_key
        )

        return discovery_service

    def test_stops_after_ten_known_in_a_row(self):
        source_config = _ukgov_style_config()

        # 5 new, then 10 known in a row -> must stop exactly there.
        known_flags = (
            [False] * 5
            + [True] * 10
            + [False] * 5
        )

        discovery_service = (
            self._discovery_service_returning(
                known_flags
            )
        )

        stop_check = (
            module.MediaAcquisitionService
            ._build_api_stop_check(
                discovery_service=discovery_service,
                source_config=source_config,
            )
        )

        items = [
            {"content_id": str(i)}
            for i in range(len(known_flags))
        ]

        assert stop_check(items) is True

        # Must not have looked past the 15th record (5 new + 10 known).
        assert (
            discovery_service.check_record_key.call_count
            == 15
        )

    def test_scattered_duplicates_never_reach_ten_in_a_row(self):
        source_config = _ukgov_style_config()

        known_flags = [
            False,
            True,
            False,
            True,
            False,
            True,
            False,
            True,
        ]

        discovery_service = (
            self._discovery_service_returning(
                known_flags
            )
        )

        stop_check = (
            module.MediaAcquisitionService
            ._build_api_stop_check(
                discovery_service=discovery_service,
                source_config=source_config,
            )
        )

        items = [
            {"content_id": str(i)}
            for i in range(len(known_flags))
        ]

        assert stop_check(items) is False

        assert (
            discovery_service.check_record_key.call_count
            == len(known_flags)
        )

    def test_missing_identity_field_is_skipped_not_fatal(self):
        # A record missing content_id must not crash the page, and must not
        # count toward (or reset) the consecutive-known streak.
        source_config = _ukgov_style_config()

        discovery_service = MagicMock()

        discovery_service.check_record_key.side_effect = (
            [(True, False)] * 9 + [(True, True)]
        )

        stop_check = (
            module.MediaAcquisitionService
            ._build_api_stop_check(
                discovery_service=discovery_service,
                source_config=source_config,
            )
        )

        items = (
            [{"content_id": str(i)} for i in range(5)]
            + [{"title": "no content_id here"}]
            + [{"content_id": str(i)} for i in range(5, 10)]
        )

        result = stop_check(items)

        assert result is True
        # The malformed record must have been skipped, not passed through
        # to the discovery service.
        assert (
            discovery_service.check_record_key.call_count
            == 10
        )
