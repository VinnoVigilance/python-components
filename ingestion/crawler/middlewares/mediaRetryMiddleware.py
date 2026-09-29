import asyncio

from scrapy.downloadermiddlewares.retry import RetryMiddleware
from scrapy.utils.response import response_status_message


class MediaRetryMiddleware(RetryMiddleware):
    """
    Retry middleware for Adverse Media.

    Behaviour for temporary source errors:

        first 521/522/524
            -> pause entire crawler for 60 seconds

        second retry failure
            -> pause entire crawler for 600 seconds

        third retry failure
            -> pause entire crawler for 1800 seconds

    Other retryable HTTP status codes use normal Scrapy retry behaviour.
    """

    BACKOFF_STATUSES = {
        521,
        522,
        524,
    }

    RETRY_DELAYS = {
        1: 60,      # 1 minute
        2: 600,     # 10 minutes
        3: 1800,    # 30 minutes
    }

    def __init__(self, settings):
        super().__init__(settings)

        self.crawler = None
        self._pause_lock = asyncio.Lock()

    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls(crawler.settings)
        middleware.crawler = crawler
        return middleware

    async def process_response(
        self,
        request,
        response,
    ):
        """
        Handle retryable HTTP responses.
        """

        if request.meta.get(
            "dont_retry",
            False,
        ):
            return response

        if (
            response.status
            not in self.retry_http_codes
        ):
            return response

        reason = response_status_message(
            response.status
        )

        retry_number = (
            request.meta.get(
                "retry_times",
                0,
            )
            + 1
        )

        # Retry limit already reached
        if (
            retry_number
            > self.max_retry_times
        ):
            return response

        # =================================================
        # Normal Scrapy retry
        # =================================================

        if (
            response.status
            not in self.BACKOFF_STATUSES
        ):
            retry_request = self._retry(
                request,
                reason,
            )

            if retry_request is not None:
                return retry_request

            return response

        # =================================================
        # Source-wide pause
        # =================================================

        delay = self.RETRY_DELAYS.get(
            retry_number,
            1800,
        )

        async with self._pause_lock:
            spider = (
                self.crawler.spider
                if self.crawler
                else None
            )

            if spider is not None:
                spider.logger.warning(
                    "Temporary HTTP %s. "
                    "Pausing entire Media crawler "
                    "for %s seconds. "
                    "retry=%s/%s "
                    "url=%s",
                    response.status,
                    delay,
                    retry_number,
                    self.max_retry_times,
                    request.url,
                )

            if self.crawler is not None:
                self.crawler.engine.pause()

            try:
                await asyncio.sleep(delay)

            finally:
                if self.crawler is not None:
                    self.crawler.engine.unpause()

                if spider is not None:
                    spider.logger.info(
                        "Media crawler resumed "
                        "after %s seconds.",
                        delay,
                    )

        retry_request = self._retry(
            request,
            reason,
        )

        if retry_request is not None:
            return retry_request

        return response