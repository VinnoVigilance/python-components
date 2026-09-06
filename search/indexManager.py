import logging
from typing import Any

from elasticsearch import Elasticsearch


logger = logging.getLogger(__name__)


def ensure_index(
    client: Elasticsearch,
    schema: dict[str, Any],
) -> str:
    """
    Ensure that the physical Elasticsearch index exists.

    This function does not change the alias.
    The alias must be switched only after a successful synchronization.
    """

    index_config = schema["index"]

    alias_name = index_config["alias"]
    version = index_config["version"]

    index_name = f"{alias_name}-{version}"

    if not client.indices.exists(index=index_name):
        _create_index(
            client=client,
            index_name=index_name,
            schema=schema,
        )

    return index_name


def switch_alias(
    client: Elasticsearch,
    alias_name: str,
    index_name: str,
) -> None:
    """
    Atomically point the alias to the requested physical index.

    If the alias already points only to that index, nothing changes.
    """

    current_index_names = _find_alias_indices(
        client=client,
        alias_name=alias_name,
    )

    if current_index_names == {index_name}:
        return

    actions: list[dict[str, Any]] = []

    for current_index_name in current_index_names:
        actions.append(
            {
                "remove": {
                    "index": current_index_name,
                    "alias": alias_name,
                }
            }
        )

    actions.append(
        {
            "add": {
                "index": index_name,
                "alias": alias_name,
            }
        }
    )

    client.indices.update_aliases(actions=actions)

    logger.info(
        "Elasticsearch alias switched. alias=%s index=%s",
        alias_name,
        index_name,
    )


def _create_index(
    client: Elasticsearch,
    index_name: str,
    schema: dict[str, Any],
) -> None:
    """
    Create a physical Elasticsearch index.
    """

    logger.info(
        "Creating Elasticsearch index: %s",
        index_name,
    )

    client.indices.create(
        index=index_name,
        settings=schema.get("settings", {}),
        mappings=schema["mapping"],
    )

    logger.info(
        "Elasticsearch index created: %s",
        index_name,
    )


def _find_alias_indices(
    client: Elasticsearch,
    alias_name: str,
) -> set[str]:
    """
    Return the physical indices currently connected to an alias.
    """

    if not client.indices.exists_alias(name=alias_name):
        return set()

    response = client.indices.get_alias(name=alias_name)

    return set(response.keys())