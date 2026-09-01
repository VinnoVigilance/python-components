from collections.abc import Iterable
from typing import Any

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk


def bulk_index_members(
    client: Elasticsearch,
    index_name: str,
    documents: Iterable[dict[str, Any]],
) -> tuple[int, list]:
    """
    Bulk index watchlist documents into Elasticsearch.

    The stable vv_member_id is used as the Elasticsearch document _id.
    """

    actions = (
        {
            "_op_type": "index",
            "_index": index_name,
            "_id": document["record_id"],
            "_source": document,
        }
        for document in documents
    )

    success_count, errors = bulk(
        client,
        actions,
        raise_on_error=False,
        raise_on_exception=False,
    )

    return success_count, errors


def get_member(
    client: Elasticsearch,
    index_name: str,
    record_id: str,
) -> dict[str, Any] | None:
    """Get one watchlist document from Elasticsearch."""

    if not client.exists(
        index=index_name,
        id=record_id,
    ):
        return None

    response = client.get(
        index=index_name,
        id=record_id,
    )

    return response["_source"]


def delete_member(
    client: Elasticsearch,
    index_name: str,
    record_id: str,
) -> bool:
    """Delete one watchlist member from Elasticsearch."""

    if not client.exists(
        index=index_name,
        id=record_id,
    ):
        return False

    client.delete(
        index=index_name,
        id=record_id,
    )

    return True