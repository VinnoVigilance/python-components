from datetime import date
from unittest.mock import Mock

import pytest

from services.common.incrementalDeltaScan import destination_delta_window, iter_effective_dates


def test_destination_window_includes_every_day_in_order():
    cursor = Mock()
    cursor.fetchone.return_value = (date(2026, 11, 16),)
    read_latest_date = Mock(return_value=date(2026, 11, 13))

    window = destination_delta_window(cursor, read_latest_date)

    read_latest_date.assert_called_once_with(cursor)
    cursor.execute.assert_called_once_with("SELECT CURRENT_DATE")
    assert list(iter_effective_dates(*window)) == [
        date(2026, 11, day) for day in (13, 14, 15, 16)
    ]


def test_explicit_end_date_avoids_database_date_lookup():
    cursor = Mock()
    day = date(2026, 11, 13)

    window = destination_delta_window(cursor, lambda _: day, through=day)

    cursor.execute.assert_not_called()
    assert list(iter_effective_dates(*window)) == [day]


@pytest.mark.parametrize(
    ("start", "end"),
    [(None, date(2026, 11, 16)), (date(2026, 11, 13), None),
     (date(2026, 11, 16), date(2026, 11, 13))],
)
def test_missing_or_reversed_bounds_yield_no_dates(start, end):
    assert list(iter_effective_dates(start, end)) == []
