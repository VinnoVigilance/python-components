import logging
from time import perf_counter
from typing import Any

from tqdm import tqdm

from infrastructure.database.connection import connection_pool
from infrastructure.search.elasticsearchClient import (
    close_elasticsearch_client,
    create_elasticsearch_client,
)
from repositories import coreMemberRepository
from repositories.search import watchlistElasticsearchRepository
from search.documentBuilder import build_watchlist_document
from search.indexManager import ensure_index, switch_alias
from search.schemaLoader import (
    build_index_name,
    load_search_schema,
)


logger = logging.getLogger(__name__)


DEFAULT_BATCH_SIZE = 1000


def run_sync(
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> dict[str, int | float | bool]:
    """
    Synchronize active PostgreSQL watchlist members with Elasticsearch.

    PostgreSQL is the source of truth.

    When dry_run is True, changes are calculated but not applied.
    """

    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero.")

    started_at = perf_counter()

    elastic_client = create_elasticsearch_client()

    connection = None
    cursor = None
    progress_bar = None

    total_count = 0
    processed_count = 0
    added_count = 0
    updated_count = 0
    skipped_count = 0
    deleted_count = 0
    last_member_id = 0

    try:
        schema = load_search_schema("watchlist")

        alias_name = schema["index"]["alias"]
        index_name = build_index_name(schema)

        index_exists = bool(
            elastic_client.indices.exists(
                index=index_name,
            )
        )

        if not dry_run:
            index_name = ensure_index(
                client=elastic_client,
                schema=schema,
            )

            index_exists = True

        logger.info(
            "Watchlist search synchronization started. "
            "index=%s batch_size=%s dry_run=%s",
            index_name,
            batch_size,
            dry_run,
        )

        if index_exists:
            elasticsearch_record_ids = set(
                watchlistElasticsearchRepository.iterate_document_ids(
                    client=elastic_client,
                    index_name=index_name,
                )
            )
        else:
            elasticsearch_record_ids = set()

        initial_elasticsearch_count = len(
            elasticsearch_record_ids
        )

        connection = connection_pool.getconn()
        cursor = connection.cursor()

        total_count = (
            coreMemberRepository.count_current_members(
                cursor=cursor,
            )
        )

        progress_bar = tqdm(
            total=total_count,
            desc="Watchlist Sync",
            unit="record",
            dynamic_ncols=True,
        )

        while True:
            members = coreMemberRepository.find_current_members_batch(
                cursor=cursor,
                last_member_id=last_member_id,
                batch_size=batch_size,
            )

            if not members:
                break

            record_ids = [
                str(member["vv_member_id"])
                for member in members
            ]

            if index_exists:
                elasticsearch_hashes = (
                    watchlistElasticsearchRepository.find_document_hashes(
                        client=elastic_client,
                        index_name=index_name,
                        record_ids=record_ids,
                    )
                )
            else:
                elasticsearch_hashes = {}

            actions: list[dict[str, Any]] = []

            batch_added_count = 0
            batch_updated_count = 0
            batch_skipped_count = 0

            for member in members:
                record_id = str(member["vv_member_id"])
                database_hash = member["record_hash"]

                elasticsearch_record_ids.discard(record_id)

                if record_id not in elasticsearch_hashes:
                    batch_added_count += 1

                    if not dry_run:
                        document = build_watchlist_document(member)

                        actions.append(
                            {
                                "_op_type": "index",
                                "_index": index_name,
                                "_id": record_id,
                                "_source": document,
                            }
                        )

                    continue

                elasticsearch_hash = elasticsearch_hashes[record_id]

                if elasticsearch_hash != database_hash:
                    batch_updated_count += 1

                    if not dry_run:
                        document = build_watchlist_document(member)

                        actions.append(
                            {
                                "_op_type": "index",
                                "_index": index_name,
                                "_id": record_id,
                                "_source": document,
                            }
                        )

                    continue

                batch_skipped_count += 1

            if not dry_run:
                success_count, errors = (
                    watchlistElasticsearchRepository.bulk_apply_actions(
                        client=elastic_client,
                        actions=actions,
                    )
                )

                if errors:
                    logger.error(
                        "Elasticsearch synchronization errors: %s",
                        errors[:5],
                    )

                    raise RuntimeError(
                        "Some watchlist documents could not be "
                        "synchronized with Elasticsearch."
                    )

                expected_success_count = (
                    batch_added_count + batch_updated_count
                )

                if success_count != expected_success_count:
                    raise RuntimeError(
                        "Elasticsearch bulk operation returned an "
                        "unexpected success count."
                    )

            processed_count += len(members)
            added_count += batch_added_count
            updated_count += batch_updated_count
            skipped_count += batch_skipped_count

            last_member_id = members[-1]["id"]

            progress_bar.update(len(members))

        if (
            processed_count == 0
            and initial_elasticsearch_count > 0
        ):
            raise RuntimeError(
                "PostgreSQL returned zero active watchlist members while "
                "Elasticsearch contains documents. Synchronization was "
                "stopped to prevent deleting the entire index."
            )

        deleted_count = len(elasticsearch_record_ids)

        if not dry_run:
            delete_actions = (
                {
                    "_op_type": "delete",
                    "_index": index_name,
                    "_id": record_id,
                }
                for record_id in elasticsearch_record_ids
            )

            successful_delete_count, delete_errors = (
                watchlistElasticsearchRepository.bulk_apply_actions(
                    client=elastic_client,
                    actions=delete_actions,
                )
            )

            if delete_errors:
                logger.error(
                    "Elasticsearch deletion errors: %s",
                    delete_errors[:5],
                )

                raise RuntimeError(
                    "Some obsolete watchlist documents could not be "
                    "deleted from Elasticsearch."
                )

            if successful_delete_count != deleted_count:
                raise RuntimeError(
                    "Elasticsearch bulk delete returned an unexpected "
                    "success count."
                )

            switch_alias(
                client=elastic_client,
                alias_name=alias_name,
                index_name=index_name,
            )

    except Exception:
        logger.exception(
            "Watchlist search synchronization failed."
        )
        raise

    finally:
        if progress_bar is not None:
            progress_bar.close()

        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection_pool.putconn(connection)

        close_elasticsearch_client(elastic_client)

    duration_seconds = perf_counter() - started_at

    result = {
        "dry_run": dry_run,
        "total_count": total_count,
        "processed_count": processed_count,
        "added_count": added_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "deleted_count": deleted_count,
        "duration_seconds": round(duration_seconds, 2),
    }

    logger.info(
        "Watchlist search synchronization finished. result=%s",
        result,
    )

    return result