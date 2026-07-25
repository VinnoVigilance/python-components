"""
Unit tests for repositories/watchlistFileLogRepository.py

These are the SAFE way to test database code: instead of a real database we
hand the function a *fake* cursor (a Mock). Nothing connects, nothing is
written anywhere -- we simply check that the function sends the right SQL with
the right values and returns what the database would hand back.

This is why these tests can run on the CI robot with no database at all, and
why they can never damage your real data.
"""

from unittest.mock import MagicMock

import pytest

from repositories import watchlistFileLogRepository

pytestmark = pytest.mark.unit


def test_insert_file_log_returns_new_id():
    # A fake cursor that pretends the database returned a new row id of 42.
    cursor = MagicMock()
    cursor.fetchone.return_value = (42,)

    new_id = watchlistFileLogRepository.insert_file_log(
        cursor=cursor,
        file_id=7,
        step="PARSING",
        status="SUCCESS",
        message="done",
        duration_ms=123,
    )

    assert new_id == 42


def test_insert_file_log_passes_all_values_in_order():
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)

    watchlistFileLogRepository.insert_file_log(
        cursor=cursor,
        file_id=7,
        step="PARSING",
        status="FAILED",
        message="boom",
        error_code="E1",
        error_details="stack",
        duration_ms=50,
    )

    # cursor.execute is called as execute(sql, params). Grab the params tuple.
    _, params = cursor.execute.call_args.args
    assert params == (7, "PARSING", "FAILED", "boom", "E1", "stack", 50)


def test_insert_file_log_targets_the_log_table():
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)

    watchlistFileLogRepository.insert_file_log(
        cursor=cursor,
        file_id=1,
        step="DOWNLOAD",
        status="STARTED",
    )

    sql = cursor.execute.call_args.args[0]
    assert "raw.watchlist_file_log" in sql
