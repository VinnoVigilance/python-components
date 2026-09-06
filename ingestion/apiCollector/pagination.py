"""Pure paging helpers for the API collector: plain data in, plain data out, no
I/O -- adapting to a new API means changing config strings, not this code."""

from typing import Any, Dict, List


def build_query(
    pagination: Dict[str, Any],
    params: Dict[str, Any],
    page: int,
) -> Dict[str, Any]:
    """Build one page's query: static ``params`` plus the declared page controls
    (a ``type: "none"`` source gets no page param, so it is fetched once)."""

    query: Dict[str, Any] = dict(params or {})

    if pagination.get("type") == "none":
        return query

    page_param = pagination.get("page_param", "page")
    size_param = pagination.get("size_param")
    page_size = pagination.get("page_size")

    query[page_param] = page

    if size_param and page_size:
        query[size_param] = page_size

    return query


def extract_items(payload: Any, items_path: str) -> List[Any]:
    """Pull the record list out of a response by dot-path (empty path = the
    payload is the list). ``[]`` when missing or not a list."""

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
    """Paging is done on an empty page."""

    return not items


def no_more_pages(
    fetched: int,
    page_count: int,
    cap: Any,
    page_size: Any,
) -> bool:
    """Stop after a page once the cap is reached (a capped query has no more) or a
    short page is seen (the last one). Falsy cap/page_size skips that check."""

    if cap and fetched >= cap:
        return True

    if page_size and page_count < page_size:
        return True

    return False


def read_path(obj: Any, path: str) -> Any:
    """Read a nested value by dot-path (``"a.b"`` -> ``obj["a"]["b"]``); None when
    any step is missing or not a dict."""

    if not path:
        return None

    current: Any = obj

    for key in path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None

    return current
