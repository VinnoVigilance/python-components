from urllib.parse import urldefrag, urljoin, urlsplit


class PaginationError(ValueError):
    """Pagination cannot continue safely or completely."""


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

