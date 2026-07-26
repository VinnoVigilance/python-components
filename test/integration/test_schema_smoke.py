"""
Smoke test for the committed schema (db/schema/vigilance_core_standard_v2_phase1.sql).

This does not test behaviour -- it proves the schema that CI loaded actually
contains the schemas, tables, columns and functions the application code writes
to. It is the tripwire for *drift*: if a future schema drop renames or removes
something the repositories still reference, this fails immediately with a clear
message instead of every downstream test failing cryptically.

Runs only against the real test database (see conftest.py).
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]


# (schema, table) pairs the pipeline depends on.
EXPECTED_TABLES = [
    ("common", "lkup_entity_type"),
    ("common", "lkup_source"),
    ("common", "lkup_source_list_type"),
    ("raw", "watchlist_file"),
    ("raw", "watchlist_file_log"),
    ("raw", "unparsed_watchlist_payload"),
    ("core", "watchlist_member"),
    ("core", "member_name"),
    ("core", "member_alias"),
    ("core", "member_identifier"),
    ("delivery", "watchlist_daily_delta_actions"),
]

# Columns core.watchlist_member must expose -- these are exactly the columns the
# versioning logic in coreMemberRepository reads and writes.
EXPECTED_MEMBER_COLUMNS = {
    "id",
    "vv_member_id",
    "source_id",
    "list_type_id",
    "external_id",
    "entity_type_id",
    "version_no",
    "is_current",
    "record_hash",
    "valid_from",
    "valid_to",
    "change_type",
    "full_payload",
}


@pytest.mark.parametrize("schema_name,table_name", EXPECTED_TABLES)
def test_expected_table_exists(db_cursor, schema_name, table_name):
    db_cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        """,
        (schema_name, table_name),
    )
    assert db_cursor.fetchone() is not None, (
        f"expected table {schema_name}.{table_name} is missing from the loaded "
        "schema -- did the schema drop change?"
    )


def test_watchlist_member_has_required_columns(db_cursor):
    db_cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'core' AND table_name = 'watchlist_member'
        """
    )
    actual = {row[0] for row in db_cursor.fetchall()}
    missing = EXPECTED_MEMBER_COLUMNS - actual
    assert not missing, f"core.watchlist_member is missing columns: {sorted(missing)}"


def test_uuid_v7_default_function_is_callable(db_cursor):
    """insert_new_member relies on the vv_member_id DEFAULT gen_random_uuid_v7()."""
    db_cursor.execute("SELECT gen_random_uuid_v7()")
    value = db_cursor.fetchone()[0]
    assert value is not None
