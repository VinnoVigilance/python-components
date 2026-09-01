import logging
from time import perf_counter

from infrastructure.database.connection import connection_pool
from infrastructure.search.elasticsearchClient import (
    close_elasticsearch_client,
    create_elasticsearch_client,
)
from repositories import coreMemberRepository
from repositories import watchlistElasticsearchRepository
from search.documentBuilder import build_watchlist_document
from search.indexManager import ensure_index
from search.schemaLoader import load_search_schema


logger = logging.getLogger(__name__)


DEFAULT_BATCH_SIZE = 1000


def run_initial_load(
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_rows: int | None = None,
) -> dict[str, int | float]:
    """
    Index all current canonical watchlist members into Elasticsearch.

    PostgreSQL remains the source of truth.
    Elasticsearch stores the current searchable representation.

    Args:
        batch_size:
            Number of PostgreSQL records to read per batch.

        max_rows:
            Optional limit used mainly for testing.
            If None, all current records are processed.

    Returns:
        Dictionary containing indexing statistics.
    """

    started_at = perf_counter()

    schema = load_search_schema("watchlist")

    elastic_client = create_elasticsearch_client()

    connection = None
    cursor = None

    processed = 0
    indexed = 0
    failed = 0
    last_member_id = 0

    try:
        index_name = ensure_index(
            client=elastic_client,
            schema=schema,
        )

        logger.info(
            "Watchlist Elasticsearch initial load started. "
            "index=%s batch_size=%s max_rows=%s",
            index_name,
            batch_size,
            max_rows,
        )

        connection = connection_pool.getconn()
        cursor = connection.cursor()

        while True:
            members = (
                coreMemberRepository.find_current_members_batch(
                    cursor=cursor,
                    last_member_id=last_member_id,
                    batch_size=batch_size,
                )
            )

            if not members:
                break

            if max_rows is not None:
                remaining = max_rows - processed

                if remaining <= 0:
                    break

                members = members[:remaining]

            documents = [
                build_watchlist_document(member)
                for member in members
            ]

            success_count, errors = (
                watchlistElasticsearchRepository.bulk_index_members(
                    client=elastic_client,
                    index_name=index_name,
                    documents=documents,
                )
            )

            processed += len(members)
            indexed += success_count
            failed += len(errors)

            last_member_id = members[-1]["id"]

            logger.info(
                "Watchlist Elasticsearch batch completed. "
                "processed=%s indexed=%s failed=%s "
                "last_member_id=%s",
                processed,
                indexed,
                failed,
                last_member_id,
            )

            if errors:
                logger.error(
                    "Elasticsearch bulk indexing errors: %s",
                    errors[:5],
                )

            if (
                max_rows is not None
                and processed >= max_rows
            ):
                break

    except Exception:
        logger.exception(
            "Watchlist Elasticsearch initial load failed."
        )
        raise

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection_pool.putconn(connection)

        close_elasticsearch_client(
            elastic_client
        )

    duration_seconds = perf_counter() - started_at

    result = {
        "processed": processed,
        "indexed": indexed,
        "failed": failed,
        "duration_seconds": round(
            duration_seconds,
            2,
        ),
    }

    logger.info(
        "Watchlist Elasticsearch initial load finished. "
        "result=%s",
        result,
    )

    return result