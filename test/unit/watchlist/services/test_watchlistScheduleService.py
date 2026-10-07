"""Unit tests for services/watchlistPipeline/watchlistScheduleService.py."""

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("DB_PASSWORD", "test_dummy")

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS  # noqa: E402
from services.watchlistPipeline.watchlistScheduleService import decide_run  # noqa: E402

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def _at(year, month, day):
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def _schedules(configs):
    for name, config in configs.items():
        if not isinstance(config, dict):
            continue
        if "schedule" in config:
            yield name, config["schedule"]
        else:
            yield from _schedules(config)


@pytest.mark.parametrize("name,schedule", list(_schedules(WATCHLIST_CONFIGS)))
def test_every_configured_schedule_is_supported(name, schedule):
    assert decide_run(schedule, None, NOW, None).should_run


def test_first_run_is_always_due():
    decision = decide_run("monthly", None, NOW, None)
    assert decision.should_run
    assert decision.reason == "no previous successful check"


@pytest.mark.parametrize(
    "schedule,last_check,due",
    [
        ("daily", _at(2026, 10, 7), False),
        ("daily", _at(2026, 10, 6), True),
        ("weekly", _at(2026, 10, 5), False),
        ("weekly", _at(2026, 10, 4), True),
        ("monthly", _at(2026, 10, 1), False),
        ("monthly", _at(2026, 9, 30), True),
        ("every_3_months", _at(2026, 7, 8), False),
        ("every_3_months", _at(2026, 7, 7), True),
    ],
)
def test_schedule_decides_due_from_last_successful_check(schedule, last_check, due):
    assert decide_run(schedule, last_check, NOW, None).should_run is due


@pytest.mark.parametrize("schedule", ["", "every_3_month", "every_0_days", "yearly"])
def test_unsupported_schedule_is_rejected(schedule):
    with pytest.raises(ValueError):
        decide_run(schedule, None, NOW, None)
