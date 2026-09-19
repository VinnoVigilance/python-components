"""Session-level lock shared by all scheduled PostgreSQL-backed jobs."""

import logging
from contextlib import contextmanager

from infrastructure.database.connection import connection_pool


logger = logging.getLogger(__name__)


@contextmanager
def advisory_job_lock(lock_id: int):
    """Prevent concurrent invocations of the same job across hosts."""
    connection = connection_pool.getconn()
    acquired = False

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s)",
                (lock_id,),
            )
            acquired = cursor.fetchone()[0]

        connection.commit()
        yield acquired

    finally:
        if acquired and not connection.closed:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (lock_id,),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                logger.exception(
                    "Failed to release job lock id=%s",
                    lock_id,
                )

        connection_pool.putconn(connection)