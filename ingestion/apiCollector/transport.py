"""Default open-API transport. The browser transport lives in
``browserTransport.py`` and is imported lazily."""

import requests


class RequestsTransport:
    """A single ``requests`` GET returning parsed JSON."""

    def __init__(self, headers=None, timeout=30):
        self._headers = headers or {"User-Agent": "Mozilla/5.0"}
        self._timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def get_json(self, url, params=None):
        """Fetch and parse one JSON response; a 401/403 fails loudly."""

        response = requests.get(
            url,
            params=params,
            headers=self._headers,
            timeout=self._timeout,
        )

        if response.status_code in (401, 403):
            raise RuntimeError(
                f"API authentication failed ({response.status_code}) for "
                f"{url}. A key/token may have rotated, or the source needs a "
                f"browser transport."
            )

        response.raise_for_status()

        return response.json()
