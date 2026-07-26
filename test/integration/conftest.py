"""
Shared setup for INTEGRATION tests that talk to a real PostgreSQL database.

Read this before writing any database test -- it is the safety contract.

Three layers protect your data:

  1. OPT-IN. These tests do nothing unless you explicitly point them at a test
     database via the TEST_DATABASE_URL environment variable. A plain `pytest`
     run never connects to any database, so it can never touch your real one.

  2. NAME GUARD. Even when TEST_DATABASE_URL is set, the harness refuses to run
     if the database name does not look like a throwaway test database (it must
     contain "test"), and hard-refuses a known production name. A fat-fingered
     connection string cannot silently hit production.

  3. ROLLBACK. Every test runs inside a transaction that is ALWAYS rolled back
     at the end. Even against the test database, nothing a test writes is ever
     committed -- the database is left exactly as it was found.

To run these locally:

    # 1. create a scratch database that has your schema, e.g.
    createdb vinno_vigilance_test        # then load your schema into it
    # 2. point the tests at it and run only the db tier
    TEST_DATABASE_URL="postgresql://postgres:pw@localhost:5432/vinno_vigilance_test" \
        pytest -m db
"""

import os
from urllib.parse import urlparse

import pytest

# A connection string must NOT resolve to one of these, no matter what.
FORBIDDEN_DB_NAMES = {"vinno_vigilance", "postgres"}

# The test database name must contain this token, as a positive safety signal.
REQUIRED_NAME_TOKEN = "test"


def _database_name(url: str) -> str:
    # urlparse gives "/dbname" as the path; strip the leading slash.
    return urlparse(url).path.lstrip("/")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The vetted test-database URL, or skip the whole db tier if absent."""
    url = os.getenv("TEST_DATABASE_URL")

    if not url:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- skipping database integration "
            "tests. See test/integration/conftest.py for how to run them."
        )

    db_name = _database_name(url).lower()

    if db_name in FORBIDDEN_DB_NAMES:
        # Loud failure, never a silent pass -- this is the last line of defence.
        pytest.fail(
            f"Refusing to run tests against database {db_name!r}: it is a "
            "protected (production) name. Use a dedicated test database."
        )

    if REQUIRED_NAME_TOKEN not in db_name:
        pytest.fail(
            f"Refusing to run tests against database {db_name!r}: its name "
            f"must contain {REQUIRED_NAME_TOKEN!r} to prove it is a throwaway "
            "test database."
        )

    return url


@pytest.fixture()
def db_connection(test_database_url):
    """A real psycopg2 connection whose work is always rolled back."""
    import psycopg2

    connection = psycopg2.connect(test_database_url)
    connection.autocommit = False  # we control the transaction ourselves

    try:
        yield connection
    finally:
        # Undo everything the test did, then hand the connection back.
        connection.rollback()
        connection.close()


@pytest.fixture()
def db_cursor(db_connection):
    """A cursor on the rolled-back connection -- the normal handle for tests."""
    with db_connection.cursor() as cursor:
        yield cursor


# ---------------------------------------------------------------------------
# Seed data.
#
# core.watchlist_member has NOT NULL foreign keys to a source, a list type, an
# entity type and a raw file. Before any member-level test can insert a row,
# those parent rows must exist. This fixture creates a minimal, self-consistent
# set of them and hands back their ids.
#
# It is written INSIDE the test's transaction (via db_cursor), so like every
# other test write it is rolled back at the end -- it never persists.
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_parents(db_cursor):
    """Insert the parent rows a watchlist_member needs and return their ids.

    Returns a dict with: entity_type_id, source_id, list_type_id,
    watchlist_file_id, raw_member_id.
    """
    from psycopg2.extras import Json

    db_cursor.execute(
        """
        INSERT INTO common.lkup_entity_type (name, description)
        VALUES ('Individual', 'test entity type')
        RETURNING id
        """
    )
    entity_type_id = db_cursor.fetchone()[0]

    db_cursor.execute(
        """
        INSERT INTO common.lkup_source (name, country, authority)
        VALUES ('TEST-SOURCE', 'US', 'Test Authority')
        RETURNING id
        """
    )
    source_id = db_cursor.fetchone()[0]

    db_cursor.execute(
        """
        INSERT INTO common.lkup_source_list_type (source_id, name, code)
        VALUES (%s, 'Sanctions', 'TEST-SANCTION')
        RETURNING id
        """,
        (source_id,),
    )
    list_type_id = db_cursor.fetchone()[0]

    db_cursor.execute(
        """
        INSERT INTO raw.watchlist_file (source_id, list_type_id, file_hash, status)
        VALUES (%s, %s, 'test-file-hash-0001', 'PARSED')
        RETURNING id
        """,
        (source_id, list_type_id),
    )
    watchlist_file_id = db_cursor.fetchone()[0]

    db_cursor.execute(
        """
        INSERT INTO raw.unparsed_watchlist_payload
            (watchlist_file_id, external_id, raw_json)
        VALUES (%s, 'EXT-001', %s)
        RETURNING id
        """,
        (watchlist_file_id, Json({"external_id": "EXT-001"})),
    )
    raw_member_id = db_cursor.fetchone()[0]

    return {
        "entity_type_id": entity_type_id,
        "source_id": source_id,
        "list_type_id": list_type_id,
        "watchlist_file_id": watchlist_file_id,
        "raw_member_id": raw_member_id,
    }


@pytest.fixture()
def make_member_data(seeded_parents):
    """Factory for the member_data dict coreMemberRepository.insert_* expects.

    Call with overrides, e.g. make_member_data(external_id="EXT-001",
    record_hash="H1"). Foreign keys default to the seeded parents.
    """

    def _make(**overrides):
        data = {
            "raw_file_id": seeded_parents["watchlist_file_id"],
            "raw_member_id": seeded_parents["raw_member_id"],
            "source_id": seeded_parents["source_id"],
            "list_type_id": seeded_parents["list_type_id"],
            "external_id": "EXT-001",
            "entity_type_id": seeded_parents["entity_type_id"],
            "record_hash": "HASH-V1",
            "full_payload": {"EntityType": "Individual", "Names": []},
        }
        data.update(overrides)
        return data

    return _make
