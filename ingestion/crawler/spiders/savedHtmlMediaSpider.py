import asyncio
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
            self.logger.info(
                "No Media detail pages require download "
                "from the saved listing."
            )
            return

        self.logger.info(
            "Saved Media listing produced %s "
            "detail page(s).",
            len(pending_details),
        )

        # SeleniumBase manages its own event loop. Running it directly in
        # Scrapy's asyncio loop causes an event-loop conflict. Move the whole
        # blocking batch to one worker thread. Details are still fetched
        # sequentially with one browser session (there is no per-item thread).
        detail_results = await asyncio.to_thread(
            lambda: list(
                self._fetch_and_parse_details(
                    pending_details
                )
            )
        )

        for result in detail_results:
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

                    "identity_fields": (
                        callback_arguments.get(
                            "identity_fields"
                        )
                        or {}
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
            if detail_response is None:
                failed_record = {
                    "record_key": item.get(
                        "record_key"
                    ),
                    "is_known": item.get(
                        "is_known"
                    ),
                    "detail_file_path": None,
                    "extracted": None,
                    "failed": True,
                    "error": item.get(
                        "fetch_error",
                        "Media detail fetch failed.",
                    ),
                    "error_stage": "DETAIL_FETCH",
                    "error_type": item.get(
                        "fetch_error_type",
                        "DetailFetchError",
                    ),
                }

                self.records.append(
                    failed_record
                )

                yield failed_record
                continue

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

                identity_fields=item.get(
                    "identity_fields"
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
            )
        )

        return (
            self.storage.detail_path
            / f"{file_name_id}.html"
        )
