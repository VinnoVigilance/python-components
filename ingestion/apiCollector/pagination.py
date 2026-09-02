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

    # A source declared ``type: "none"`` is fetched in a single request. Adding
    # a page parameter here would be harmful: an endpoint that ignores paging
    # returns the full list on every page, so the caller would loop forever.
    # Emit only the static params in that case.
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


def no_more_pages(
    fetched: int,
    page_count: int,
    cap: Any,
    page_size: Any,
) -> bool:
    """
    Decide whether to stop *after* yielding a page, for a capped list API.

    Complements ``should_stop`` (which stops on an empty page *before* yielding).
    Two conditions end the loop:
      * ``fetched >= cap`` -- a capped query returns at most ``cap`` records, so
        once that many are in hand there is nothing more to retrieve. This is the
        guard against endpoints (e.g. Interpol) that answer an out-of-range page
        number with a non-empty page -- repeating the last one -- instead of an
        empty page, which would otherwise loop forever.
      * ``page_count < page_size`` -- a short page is the last page.
    ``cap`` / ``page_size`` may be falsy (not configured), in which case that
    condition is simply skipped.
    """

    if cap and fetched >= cap:
        return True

    if page_size and page_count < page_size:
        return True

    return False


def read_path(obj: Any, path: str) -> Any:
    """
    Read a single value out of a nested dict by dot-path.

    ``read_path({"a": {"b": 1}}, "a.b")`` -> ``1``. Returns ``None`` when any
    step is missing or not a dict. Used to pull an item's id for detail
    hydration and its dedup key.
    """

    if not path:
        return None

    current: Any = obj

    for key in path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None

    return current


def build_detail_url(url_template: str, id_value: Any) -> str:
    """
    Fill a detail URL template's ``{id}`` placeholder with the item's id.
    """

    return url_template.format(id=id_value)


def merge_records(
    stub: Dict[str, Any],
    detail: Dict[str, Any],
    target_field: str = None,
) -> Dict[str, Any]:
    """
    Fold a detail response into its list stub, producing one record.

    ``target_field`` set -> nest the whole detail object under that key
    (``{**stub, target_field: detail}``), keeping the two shapes separate.
    ``target_field`` None -> flat merge with detail winning on shared keys.
    """

    merged = dict(stub)

    if target_field:
        merged[target_field] = detail or {}
    else:
        merged.update(detail or {})

    return merged


def assemble_record(
    stub: Dict[str, Any],
    detail: Dict[str, Any],
    shape: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a structured raw record from a stub + its detail, matching the
    crawler's shape: a top-level id, the list stub, the detail, and a list of
    attachment links.

    ``shape`` keys:
        id_path           dot-path in the stub to the record's id
        id_field          top-level key to store it under (default
                          "source_record_id")
        list_field        key to nest the stub under (default "list")
        detail_field      key to nest the detail under (default "detail")
        attachments_field key for the attachments list (default "attachments")
        attachments       rules [{type, url_path, from?}] -- url_path is read
                          from the stub, or the detail when from="detail";
                          only links that resolve are kept

    The attachment URLs are stored for a later download service; nothing is
    fetched here.
    """

    id_field = shape.get("id_field", "source_record_id")
    list_field = shape.get("list_field", "list")
    detail_field = shape.get("detail_field", "detail")
    attachments_field = shape.get("attachments_field", "attachments")

    record: Dict[str, Any] = {
        id_field: read_path(stub, shape["id_path"]),
        list_field: stub,
    }

    if detail is not None:
        record[detail_field] = detail

    attachments = []

    for rule in shape.get("attachments", []):
        source = detail if rule.get("from") == "detail" else stub
        url = read_path(source, rule["url_path"])

        if url:
            attachments.append({"type": rule["type"], "url": url})

    record[attachments_field] = attachments

    return record
