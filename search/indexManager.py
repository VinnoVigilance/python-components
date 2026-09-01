import logging
from typing import Any

from elasticsearch import Elasticsearch


logger = logging.getLogger(__name__)


def ensure_index(
    client: Elasticsearch,
    schema: dict[str, Any],
) -> str:
    """
    Ensure that the physical index and alias exist.

    Returns the physical index name.
    """

    index_config = schema["index"]

    alias_name = index_config["alias"]
    version = index_config["version"]

    index_name = (
        f"{alias_name}-{version}"
    )

    if not client.indices.exists(
        index=index_name
    ):
        _create_index(
            client=client,
            index_name=index_name,
            schema=schema,
        )

    _ensure_alias(
        client=client,
        alias_name=alias_name,
        index_name=index_name,
    )

    return index_name


def _create_index(
    client: Elasticsearch,
    index_name: str,
    schema: dict[str, Any],
) -> None:
    """Create Elasticsearch index."""

    logger.info(
        "Creating Elasticsearch index: %s",
        index_name,
    )

    settings = schema.get(
        "settings",
        {},
    )

    mapping = schema["mapping"]

    client.indices.create(
        index=index_name,
        settings=settings,
        mappings=mapping,
    )

    logger.info(
        "Elasticsearch index created: %s",
        index_name,
    )


def _ensure_alias(
    client: Elasticsearch,
    alias_name: str,
    index_name: str,
) -> None:
    """Ensure alias points to the expected index."""

    if client.indices.exists_alias(
        name=alias_name
    ):
        return

    client.indices.put_alias(
        index=index_name,
        name=alias_name,
    )

    logger.info(
        "Created Elasticsearch alias %s -> %s",
        alias_name,
        index_name,
    )