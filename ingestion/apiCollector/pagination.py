"""
Pure paging logic for the API collector.

No I/O lives here: every function takes plain data and returns plain data,
so paging behaviour can be unit-tested without a network call. Adapting to a
new API means changing config strings, not this code.
"""

from typing import Any, Dict, List


def build_query(
    pagination: Dict[str, Any],
    params: Dict[str, Any],
    page: int,
) -> Dict[str, Any]:
    """
    Build the query parameters for one page.

    Merges the source's static ``params`` with the page controls declared in
    ``pagination`` (which query key carries the page number and, optionally,
    the page size).
    """

    query: Dict[str, Any] = dict(params or {})

    page_param = pagination.get("page_param", "page")
    size_param = pagination.get("size_param")
    page_size = pagination.get("page_size")

    query[page_param] = page

    if size_param and page_size:
        query[size_param] = page_size

    return query


def extract_items(payload: Any, items_path: str) -> List[Any]:
    """
    Pull the list of records out of a parsed API response.

    ``items_path`` is a simple dot-path (e.g. ``"items"`` for FBI, or
    ``"data.results"`` for another API). An empty path means the payload is
    itself the list. Returns ``[]`` when the path is missing or not a list.
    """

    if not items_path:
        return payload if isinstance(payload, list) else []

    current: Any = payload

    for key in items_path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return []

    return current if isinstance(current, list) else []


def should_stop(items: List[Any]) -> bool:
    """
    Decide whether paging is done.

    The only terminator is an empty page: we page until the API stops
    returning records. No page number or total count is required.
    """

    return not items
