"""
Integration test for the database test HARNESS itself.

Before trusting the harness to test real repositories, we prove the safety
contract works against a real PostgreSQL: we can connect, run real SQL, and --
crucially -- that everything is rolled back so no test leaves a trace.

This uses a TEMPORARY table, which is private to the connection and disappears
automatically, so it never touches your real schema or data.

Skipped automatically unless TEST_DATABASE_URL points at a test database.
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]


def test_can_run_real_sql(db_cursor):
    db_cursor.execute("SELECT 1")
    assert db_cursor.fetchone()[0] == 1


def test_write_then_read_within_a_transaction(db_cursor):
    db_cursor.execute(
        "CREATE TEMPORARY TABLE harness_check (id int, name text)"
    )
    db_cursor.execute(
        "INSERT INTO harness_check (id, name) VALUES (%s, %s)", (1, "acme")
    )
    db_cursor.execute("SELECT name FROM harness_check WHERE id = 1")

    assert db_cursor.fetchone()[0] == "acme"


def test_rollback_leaves_no_trace(db_connection):
    """Two fresh cursors: the first writes, we roll back, the second sees nothing."""
    with db_connection.cursor() as writer:
        writer.execute("CREATE TEMPORARY TABLE rollback_check (id int)")
        writer.execute("INSERT INTO rollback_check VALUES (1)")

    db_connection.rollback()

    with db_connection.cursor() as reader:
        # After rollback the temp table is gone -- proving nothing persisted.
        with pytest.raises(Exception):
            reader.execute("SELECT * FROM rollback_check")


# ---------------------------------------------------------------------------
# TEMPLATE for testing a real repository against the test database.
#
# Once your test database has the schema loaded, copy this pattern into
# test/integration/repositories/ to exercise the real insert/query code. It
# stays safe because db_cursor rolls everything back.
#
#   from repositories import watchlistFileLogRepository
#
#   def test_insert_file_log_against_real_db(db_cursor):
#       # (insert any parent rows the foreign keys require first)
#       new_id = watchlistFileLogRepository.insert_file_log(
#           cursor=db_cursor,
#           file_id=<an existing file id>,
#           step="PARSING",
#           status="SUCCESS",
#       )
#       assert isinstance(new_id, int)
#       # nothing is committed; the row vanishes when the test ends.
# ---------------------------------------------------------------------------
