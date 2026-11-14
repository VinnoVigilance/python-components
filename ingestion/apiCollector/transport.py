"""Default open-API transport. The browser transport lives in
``browserTransport.py`` and is imported lazily."""

import re

import requests


class RequestsTransport:
    """Fetch JSON, optionally using a bearer token extracted from a page.

    ``page_token`` requires a GET page URL and a regex with the token in its
    first capture group. It does not support login flows or tokens in query
    parameters. Failed token extraction raises an error before collection.
    """

    def __init__(self, headers=None, timeout=30, page_token=None):
        self._headers = dict(headers or {"User-Agent": "Mozilla/5.0"})
        self._timeout = timeout
        self._page_token = page_token or {}

    def __enter__(self):
        if self._page_token:
            self._refresh_token()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def get_json(self, url, params=None):
        """Fetch and parse one JSON response; a 401/403 fails loudly."""

        response = self._get(url, params)

        if self._page_token and (
            response.status_code in (401, 403)
            or response.history
            or "json" not in response.headers.get("Content-Type", "").lower()
        ):
            self._refresh_token()
            response = self._get(url, params)

        if response.status_code in (401, 403):
            raise RuntimeError(
                f"API authentication failed ({response.status_code}) for "
                f"{url}. A key/token may have rotated, or the source needs a "
                f"browser transport."
            )

        response.raise_for_status()

        return response.json()

    def _get(self, url, params):
        return requests.get(
            url, params=params, headers=self._headers, timeout=self._timeout
        )

    def _refresh_token(self):
        response = requests.get(self._page_token["url"], timeout=self._timeout)
        response.raise_for_status()
        match = re.search(self._page_token["pattern"], response.text)
        if not match or not match.group(1):
            raise RuntimeError("Unable to extract API token from source page")
        self._headers["Authorization"] = f"Bearer {match.group(1)}"
