import re
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlsplit, urlunsplit


class PaginationError(ValueError):
    """Pagination cannot continue safely or completely."""


def resolve_numbered_page_url(current_url, page_value, query_param, *, visited_urls=()):
    """Build a query URL from a numeric pager attribute; keep other filters."""
    if not isinstance(query_param, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", query_param):
        raise PaginationError("Invalid next-page query parameter name")
    if not isinstance(page_value, str) or not re.fullmatch(r"[0-9]+", page_value.strip()):
        raise PaginationError("Next-page value must be a non-negative integer")
    parts = urlsplit(current_url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key != query_param]
    query.append((query_param, str(int(page_value.strip()))))
    target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    return resolve_next_page_url(current_url, target, visited_urls=visited_urls)


def resolve_next_page_url(current_url, href, *, visited_urls=()):
    """Resolve a next-page link; reject invalid links and pagination loops."""
    if href is None:
        return None

    href = str(href).strip()
    if not href:
        raise PaginationError("The next-page link is empty")

    current_url = urldefrag(current_url)[0]
    next_url = urldefrag(urljoin(current_url, href))[0]

    current = urlsplit(current_url)
    target = urlsplit(next_url)

    if target.scheme not in {"http", "https"}:
        raise PaginationError("Unsupported next-page URL scheme")

    if (
        target.scheme.lower() != current.scheme.lower()
        or target.netloc.lower() != current.netloc.lower()
    ):
        raise PaginationError("The next-page link changes origin")

    visited = {urldefrag(url)[0] for url in visited_urls}

    if next_url == current_url or next_url in visited:
        raise PaginationError("Pagination loop detected")

    return next_url

