from unittest.mock import MagicMock
from copy import deepcopy

import pytest

from services.adverseMediaPipeline import (
    mediaDiscoveryService as module,
)


pytestmark = pytest.mark.unit


def _service(
    monkeypatch,
    *,
    known_results,
    stop_after_known,
    threshold=2,
):
    exists = MagicMock(
        side_effect=known_results
    )
    monkeypatch.setattr(
        module,
        "exists_by_record_key",
        exists,
    )

    return module.MediaDiscoveryService(
        cursor=MagicMock(),
        source_id=1,
        dataset_id=2,
        source_config={},
        stop_after_known=stop_after_known,
        known_threshold=threshold,
    )


def test_initial_mode_never_stops_on_known_records(
    monkeypatch,
):
    service = _service(
        monkeypatch,
        known_results=[True, True, True],
        stop_after_known=False,
    )

    decisions = [
        service.check_record_key(str(index))
        for index in range(3)
    ]

    assert decisions == [
        (True, False),
        (True, False),
        (True, False),
    ]


def test_incremental_mode_stops_at_known_threshold(
    monkeypatch,
):
    service = _service(
        monkeypatch,
        known_results=[True, True],
        stop_after_known=True,
    )

    assert service.check_record_key("a") == (
        True,
        False,
    )
    assert service.check_record_key("b") == (
        True,
        True,
    )


def test_new_record_resets_consecutive_known_counter(
    monkeypatch,
):
    service = _service(
        monkeypatch,
        known_results=[True, False, True, True],
        stop_after_known=True,
    )

    decisions = [
        service.check_record_key(str(index))
        for index in range(4)
    ]

    assert decisions == [
        (True, False),
        (False, False),
        (True, False),
        (True, True),
    ]


def test_identity_failure_breaks_known_streak(
    monkeypatch,
):
    service = _service(
        monkeypatch,
        known_results=[True, True, True, True],
        stop_after_known=True,
        threshold=3,
    )

    assert service.check_record_key("a") == (
        True,
        False,
    )
    assert service.check_record_key("b") == (
        True,
        False,
    )

    service.record_identity_failure()

    assert service.check_record_key("c") == (
        True,
        False,
    )
    assert service.check_record_key("d") == (
        True,
        False,
    )

    summary = service.get_summary()
    assert summary["identity_failure_count"] == 1
    assert summary["completed_safely"] is False


def test_summary_distinguishes_source_end_from_threshold(
    monkeypatch,
):
    source_end_service = _service(
        monkeypatch,
        known_results=[False],
        stop_after_known=True,
        threshold=2,
    )
    source_end_service.check_record_key("new")
    source_end_service.mark_source_end()

    assert source_end_service.get_summary() == {
        "discovered_count": 1,
        "known_count": 0,
        "new_count": 1,
        "identity_failure_count": 0,
        "discovery_failure_count": 0,
        "selected_detail_count": 0,
        "reached_source_end": True,
        "stop_reason": "SOURCE_EXHAUSTED",
        "completed_safely": True,
    }


def test_resolve_mode_leaves_sources_without_overrides_unchanged():
    config = {"discovery": {"strategy": "rss"}, "acquisition": {"type": "crawler"}}
    assert module.resolve_discovery_mode(config, "INITIAL") == config


@pytest.mark.parametrize("mode,expected_recipe", [("INITIAL", "historical"), ("INCREMENTAL", "recent")])
def test_resolve_mode_applies_overrides_without_mutating_source(mode, expected_recipe):
    config = {
        "dataset_name": "EXAMPLE",
        "discovery": {"policy": "full_scan", "form_search": "recent"},
        "acquisition": {"type": "crawler", "fetch_strategy": "direct"},
        "bypass_config": {"enabled": False},
        "discovery_by_mode": {
            "INITIAL": {
                "form_search": "historical",
                "acquisition": {"fetch_strategy": "saved_html"},
                "bypass_config": {"enabled": True},
            },
            "INCREMENTAL": {"form_search": "recent"},
        },
    }
    original = deepcopy(config)

    resolved = module.resolve_discovery_mode(config, mode)

    assert config == original
    assert resolved["discovery"] == {"policy": "full_scan", "form_search": expected_recipe}
    assert resolved["acquisition"] == {
        "type": "crawler", "fetch_strategy": "saved_html" if mode == "INITIAL" else "direct",
    }
    assert resolved["bypass_config"] == {"enabled": mode == "INITIAL"}
    assert resolved["dataset_name"] == "EXAMPLE"


@pytest.mark.parametrize("failure", [None, "identity", "discovery"])
def test_completed_range_is_safe_only_without_failures(monkeypatch, failure):
    service = _service(monkeypatch, known_results=[], stop_after_known=False)
    if failure == "identity":
        service.record_identity_failure()
    elif failure == "discovery":
        service.mark_discovery_failure("FORM_SEARCH_INCOMPLETE")
    service.stop_reason = "CONFIGURED_RANGE_COMPLETE"

    summary = service.get_summary()

    assert summary["completed_safely"] is (failure is None)
    assert summary["reached_source_end"] is False
