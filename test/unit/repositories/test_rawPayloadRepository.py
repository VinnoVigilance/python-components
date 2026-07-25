"""
Unit tests for repositories/rawPayloadRepository.py

Same safe idea as the file-log repository tests: no real database. Here we also
patch psycopg2's `execute_values` (the batch-insert helper) so we can inspect
exactly what rows the repository would send, without a live connection.

The real logic worth checking is the mapping step: each incoming
(external_id, raw_json) pair must become a (watchlist_file_id, external_id,
Json(raw_json)) row, and the function must report how many rows it inserted.
"""

from unittest.mock import MagicMock, patch

import pytest

from repositories import rawPayloadRepository

pytestmark = pytest.mark.unit


def test_insert_raw_payloads_reports_row_count():
    cursor = MagicMock()
    payloads = [
        ("EID-1", {"name": "A"}),
        ("EID-2", {"name": "B"}),
        ("EID-3", {"name": "C"}),
    ]

    with patch.object(rawPayloadRepository, "execute_values"):
        count = rawPayloadRepository.insert_raw_payloads(
            cursor=cursor,
            watchlist_file_id=99,
            payloads=payloads,
        )

    assert count == 3


def test_insert_raw_payloads_builds_correct_rows():
    cursor = MagicMock()
    payloads = [("EID-1", {"name": "A"})]

    with patch.object(rawPayloadRepository, "execute_values") as execute_values:
        rawPayloadRepository.insert_raw_payloads(
            cursor=cursor,
            watchlist_file_id=99,
            payloads=payloads,
        )

    # execute_values(cursor, sql, values, page_size=...)
    call_args = execute_values.call_args
    values = call_args.args[2]

    assert len(values) == 1
    watchlist_file_id, external_id, wrapped_json = values[0]
    assert watchlist_file_id == 99
    assert external_id == "EID-1"
    # raw_json is wrapped in psycopg2's Json adapter; its .adapted holds the dict
    assert wrapped_json.adapted == {"name": "A"}


def test_insert_raw_payloads_empty_list():
    cursor = MagicMock()

    with patch.object(rawPayloadRepository, "execute_values"):
        count = rawPayloadRepository.insert_raw_payloads(
            cursor=cursor,
            watchlist_file_id=99,
            payloads=[],
        )

    assert count == 0
