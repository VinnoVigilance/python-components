import asyncio

from concurrent.futures import (
    ThreadPoolExecutor,
)
from pathlib import Path

import scrapy

from scrapy.http import HtmlResponse

from ingestion.bypassCollector.engines.stealthBrowserEngine import (
    StealthBrowserEngine,
)
from ingestion.crawler.browserDetailFetcher import (
    BrowserDetailFetcher,
)
from ingestion.crawler.spiders.mediaSpider import (
    MediaSpider,
)


_ITERATION_FINISHED = object()


def _get_next_result(iterator):
    """
    Read one result from a synchronous generator.

    StopIteration cannot safely enter an asyncio
    Future, so a sentinel is returned instead.
    """

    try:
        return next(iterator)

    except StopIteration:
        return _ITERATION_FINISHED


class SavedHtmlMediaSpider(MediaSpider):
    """
    Media Spider for protected list-detail sources.

    Flow:

        BypassCollector
            -> saves the listing HTML

        SavedHtmlMediaSpider
            -> reads the saved listing
            -> uses MediaSpider discovery logic
            -> checks known/new records
            -> applies consecutive-known threshold
            -> fetches detail pages through browser
            -> saves one HTML file per article
            -> returns Media-compatible records

    The listing HTML is not returned as a Media record.
    Only individual detail HTML files are returned.
    """

    name = "saved_html_media_spider"

    async def start(self):
        """
        Start from the listing HTML previously saved
        by BypassCollector.
        """

        source_file_path = (
            self.task.source_file_path
        )

        if not source_file_path:
            raise ValueError(
                "source_file_path is required for "
                "SavedHtmlMediaSpider."
            )

        source_file = Path(
            source_file_path
        ).resolve()

        if not source_file.is_file():
            raise FileNotFoundError(
                "Saved Media listing HTML was not "
                f"found: {source_file}"
            )

        listing_response = HtmlResponse(
            url=self.task.url,
            body=source_file.read_bytes(),
            encoding="utf-8",
        )

        pending_details = (
            self._discover_from_saved_listing(
                listing_response
            )
        )

        if not pending_details:
            raise ValueError(
                "No Media detail pages were "
                "discovered from the saved listing."
            )

        self.logger.info(
            "Saved Media listing produced %s "
            "detail page(s).",
            len(pending_details),
        )

        # BrowserDetailFetcher is synchronous because
        # Selenium is blocking. Run it in one worker
        # thread while keeping one browser session.
        iterator = (
            self._fetch_and_parse_details(
                pending_details
            )
        )

        loop = asyncio.get_running_loop()

        with ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=(
                "saved-html-media-browser"
            ),
        ) as executor:

            while True:
                result = await loop.run_in_executor(
                    executor,
                    _get_next_result,
                    iterator,
                )

                if result is _ITERATION_FINISHED:
                    break

                yield result

    def _discover_from_saved_listing(
        self,
        response: HtmlResponse,
    ) -> list[dict]:
        """
        Reuse MediaSpider.parse_listing() for:

        - extracting article URLs
        - extracting SourceRecordId
        - building record_key
        - known/new database checks
        - duplicate-link filtering
        - consecutive-known stop condition

        MediaSpider normally converts each discovered
        article into a Scrapy Request. Here we capture
        those requests and fetch them with the shared
        browser instead.
        """

        start_page = int(
            self.discovery_config.get(
                "start_page",
                1,
            )
        )

        pending_details: list[dict] = []

        outputs = super().parse_listing(
            response=response,
            page_number=start_page,
        )

        for output in outputs:

            if not isinstance(
                output,
                scrapy.Request,
            ):
                continue

            callback_arguments = dict(
                output.cb_kwargs or {}
            )

            # Detail requests contain record_key and
            # is_known. A possible next-listing-page
            # request only contains page_number and
            # must not be treated as an article.
            if (
                "record_key"
                not in callback_arguments
                or "is_known"
                not in callback_arguments
            ):
                continue

            pending_details.append(
                {
                    "detail_url": output.url,

                    "record_key": (
                        callback_arguments[
                            "record_key"
                        ]
                    ),

                    "is_known": (
                        callback_arguments[
                            "is_known"
                        ]
                    ),

                    "source_record_id": (
                        callback_arguments.get(
                            "source_record_id"
                        )
                    ),
                }
            )

        return pending_details

    def _fetch_and_parse_details(
        self,
        pending_details: list[dict],
    ):
        """
        Fetch protected detail pages and pass each
        response to MediaSpider.parse_detail().

        MediaSpider.parse_detail():

        - saves one separate HTML file
        - extracts configured Media fields
        - creates detail_file_path
        - returns the contract expected by
          MediaAcquisitionService
        """

        if self.storage is None:
            raise ValueError(
                "Crawler storage is required for "
                "SavedHtmlMediaSpider."
            )

        browser_config = (
            self.source_config.get(
                "detail_browser",
                {},
            )
        )

        storage_config = dict(
            self.source_config.get(
                "storage",
                {},
            )
        )

        # Media should fetch the current page again
        # by default so content-hash versioning can
        # detect an updated article.
        #
        # A source can explicitly enable reuse if
        # necessary.
        storage_config.setdefault(
            "reuse_saved_detail_pages",
            False,
        )

        fetcher = BrowserDetailFetcher(
            browser_config=browser_config,
            storage_config=storage_config,

            cache_path_builder=(
                self._build_detail_cache_path
            ),

            engine_factory=(
                StealthBrowserEngine
            ),

            component_logger=self.logger,
        )

        for (
            item,
            detail_response,
        ) in fetcher.fetch(
            pending_details
        ):
            # Reuse the existing Media method.
            # This is the point where a separate
            # detail HTML file is saved.
            yield from super().parse_detail(
                response=detail_response,

                record_key=item[
                    "record_key"
                ],

                is_known=item[
                    "is_known"
                ],

                source_record_id=item.get(
                    "source_record_id"
                ),
            )

    def _build_detail_cache_path(
        self,
        item: dict,
    ) -> Path:
        """
        Build the same file path that
        MediaSpider.parse_detail() uses.

        This is only used when Media cache reuse
        is explicitly enabled.
        """

        file_name_id = (
            self._build_file_name_id(
                record_key=item[
                    "record_key"
                ],
                source_record_id=item.get(
                    "source_record_id"
                ),
            )
        )

        return (
            self.storage.detail_path
            / f"{file_name_id}.html"
        )