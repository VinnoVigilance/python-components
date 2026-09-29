import logging
from collections.abc import Iterable, Iterator
from typing import Any

from elasticsearch import Elasticsearch
from elasticsearch.helpers import scan, streaming_bulk


logger = logging.getLogger(__name__)


DEFAULT_CHUNK_SIZE = 500
DEFAULT_MAX_RETRIES = 3
RETRYABLE_STATUS_CODES = (429, 502, 503, 504)


def find_document_hashes(
    client: Elasticsearch,
    index_name: str,
    record_ids: list[str],
) -> dict[str, str | None]:
    """
    Return the record_hash of existing Elasticsearch documents.

    Missing documents are not included in the result.
    """

    if not record_ids:
        return {}

    response = client.mget(
        index=index_name,
        ids=record_ids,
        source_includes=["record_hash"],
    )

    document_hashes: dict[str, str | None] = {}

    for document in response.get("docs", []):
        if not document.get("found"):
            continue

        source = document.get("_source") or {}

        document_hashes[str(document["_id"])] = source.get(
            "record_hash"
        )

    return document_hashes


def iterate_document_ids(
    client: Elasticsearch,
    index_name: str,
) -> Iterator[str]:
    """
    Yield the IDs of all documents stored in an Elasticsearch index.
    """

    documents = scan(
        client=client,
        index=index_name,
        query={
            "_source": False,
            "query": {
                "match_all": {}
            },
        },
    )

    for document in documents:
        yield str(document["_id"])


def bulk_apply_actions(
    client: Elasticsearch,
    actions: Iterable[dict[str, Any]],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[int, list[dict[str, Any]]]:
    """
    Apply Elasticsearch index and delete actions in batches.

    Temporary failures are retried automatically.
    """

    successful_count = 0
    errors: list[dict[str, Any]] = []

    results = streaming_bulk(
        client=client,
        actions=actions,
        chunk_size=chunk_size,
        max_retries=max_retries,
        initial_backoff=1,
        max_backoff=8,
        retry_on_status=RETRYABLE_STATUS_CODES,
        raise_on_error=False,
        raise_on_exception=False,
    )

    for is_successful, result in results:
        operation_name, operation_result = next(
            iter(result.items())
        )

        if is_successful:
            successful_count += 1
            continue

        errors.append(
            {
                "operation": operation_name,
                "id": operation_result.get("_id"),
                "status": operation_result.get("status"),
                "error": operation_result.get("error"),
            }
        )

    if errors:
        logger.error(
            "Elasticsearch bulk operation completed with errors. "
            "success=%s failed=%s",
            successful_count,
            len(errors),
        )

    return successful_count, errors