"""PostgreSQL connection pool.

The pool is created LAZILY -- on first use, not at import time. This matters
for two reasons:

  1. Importing any module that talks to the database (services, repositories)
     no longer tries to open a real database connection just to be imported.
     That is what lets the test suite import and exercise this code without a
     live PostgreSQL running.
  2. Configuration (including the DB password) is only required once a
     connection is actually needed, so tooling that merely imports the code
     does not have to have every secret set.

Call sites are unchanged: ``connection_pool.getconn()`` and
``connection_pool.putconn(conn)`` work exactly as before.

you can also use get_db_connection() for AUTO safe transactions with rollback on failure and release 
"""
import logging
from contextlib import contextmanager
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

class _LazyConnectionPool:
    """A stand-in for ThreadedConnectionPool that builds itself on first use."""

    def __init__(self) -> None:
        self._pool: ThreadedConnectionPool | None = None

    def _ensure_pool(self) -> ThreadedConnectionPool:
        if self._pool is None:
            # Imported here (not at module top) so that importing this module
            # does not require the database settings to be present.
            from config.settings import (
                DB_HOST,
                DB_NAME,
                DB_PASSWORD,
                DB_PORT,
                DB_USER,
            )

            self._pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=20, # Bumped maxconn to 20 to support background workers
                host=DB_HOST,
                port=DB_PORT,
                dbname=DB_NAME,
                user=DB_USER,
                password=DB_PASSWORD,
            )
        return self._pool

    def getconn(self, *args, **kwargs):
        return self._ensure_pool().getconn(*args, **kwargs)

    def putconn(self, *args, **kwargs):
        return self._ensure_pool().putconn(*args, **kwargs)

    def closeall(self) -> None:
        if self._pool is not None:
            self._pool.closeall()


# Original singleton instance
connection_pool = _LazyConnectionPool()


@contextmanager
def get_db_connection():
    """
    Context manager that yields a connection from the pool.
    Automatically commits on success, rolls back on exceptions, 
    and returns the connection to the pool.
    
    Do not requires the developers to manually write 
    try...except...finally blocks at every call site 
    to commit, rollback, and return the connection.
    """
    conn = connection_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Transaction rolled back automatically due to error: {str(e)}")
        raise e
    finally:
        connection_pool.putconn(conn)