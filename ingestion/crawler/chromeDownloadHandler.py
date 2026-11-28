"""Chrome-compatible HTTP downloads through Scrapy's normal downloader queue."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from curl_cffi import requests
from scrapy.core.downloader.handlers.base import BaseDownloadHandler
from scrapy.http import Headers, Request, Response
from scrapy.responsetypes import responsetypes


class ChromeDownloadHandler(BaseDownloadHandler):
    """Use curl_cffi for the HTTP exchange and let Scrapy handle retries."""

    def __init__(self, crawler):
        super().__init__(crawler)
        # asyncio's shared executor has its own smaller worker limit. Match
        # Scrapy's configured limit so it remains the concurrency authority.
        self._executor = ThreadPoolExecutor(
            max_workers=crawler.settings.getint("CONCURRENT_REQUESTS"),
            thread_name_prefix="chrome-download",
        )

    async def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def download_request(self, request: Request) -> Response:
        # Curl supplies the browser headers. Keep Scrapy's proxy credentials
        # out of the origin headers; curl uses its normal environment proxy.
        curl_headers = {b"user-agent", b"accept-encoding", b"proxy-authorization"}
        headers = {
            key.decode("ascii"): b", ".join(values).decode("latin-1")
            for key, values in request.headers.items()
            if key.lower() not in curl_headers
        }
        fetch = partial(
            requests.request,
            request.method,
            request.url,
            headers=headers,
            data=request.body or None,
            impersonate="chrome",
            allow_redirects=False,
            timeout=request.meta.get("download_timeout", 30),
        )
        result = await asyncio.get_running_loop().run_in_executor(self._executor, fetch)

        # Curl has already decompressed the body. Preserve repeated headers
        # such as Set-Cookie and let Scrapy process redirects and HTTP statuses.
        response_headers = Headers()
        for key, value in result.headers.multi_items():
            if key.lower() not in {"content-encoding", "content-length"}:
                response_headers.appendlist(key, value)
        response_type = responsetypes.from_args(
            headers=response_headers, url=request.url, body=result.content,
        )
        return response_type(
            url=request.url, status=result.status_code, headers=response_headers,
            body=result.content, request=request,
        )
