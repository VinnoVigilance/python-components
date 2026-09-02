"""
HTTP transports for the API collector.

A transport answers one question -- "give me the JSON at this URL" -- and
hides *how* the request is made. The collector's paging/merge logic depends
only on this small surface, so the same recipe works whether the API is open
(plain ``requests``) or behind a bot wall (a browser-backed transport).

This module holds the default, open-API transport. The browser-backed
transport lives next to the stealth engine it is built from
(``ingestion/bypassCollector/browserTransport.py``) and is imported lazily,
so a machine that never touches a protected source never loads the browser.
"""

from concurrent.futures import ThreadPoolExecutor

import requests


class RequestsTransport:
    """
    Default transport: a single ``requests`` GET returning parsed JSON.

    Usable as a context manager for symmetry with the browser transport
    (which must open and close a real browser); here it opens nothing.
    """

    def __init__(self, headers=None, timeout=30):
        self._headers = headers or {"User-Agent": "Mozilla/5.0"}
        self._timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def get_json(self, url, params=None):
        """
        Fetch and parse one JSON response.

        A 401/403 will not fix itself on retry and a silent empty result would
        look like "no records", so fail loudly and immediately -- an expired or
        rotated key/token is then obvious.
        """

        response = requests.get(
            url,
            params=params,
            headers=self._headers,
            timeout=self._timeout,
        )

        if response.status_code in (401, 403):
            raise RuntimeError(
                f"API authentication failed ({response.status_code}) for "
                f"{url}. The request was rejected -- a key/token may have "
                f"rotated, or the source needs a browser transport."
            )

        response.raise_for_status()

        return response.json()

    def get_json_many(self, urls, concurrency=10):
        """
        Fetch many JSON URLs concurrently using a small thread pool.

        Returns the parsed bodies in the same order as ``urls``.
        """

        urls = list(urls)

        if not urls:
            return []

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            return list(pool.map(self.get_json, urls))
