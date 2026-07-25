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
