import asyncio
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlparse

import scrapy

from scrapy.http import HtmlResponse

from ingestion.bypassCollector.engines.stealthBrowserEngine import (
    StealthBrowserEngine,
)
from ingestion.crawler.browserDetailFetcher import (
    BrowserDetailFetcher,
)
from ingestion.downloader.interface import download
from ingestion.downloader.models import DownloadTask
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

        if self.task.detail_items:
            pending_details = [
                {
                    "detail_url": item["detail_url"],
                    **self.detail_item_kwargs(item),
                }
                for item in self.task.detail_items
            ]

            detail_results = await asyncio.to_thread(
                lambda: list(
                    self._fetch_and_parse_details(
                        pending_details
                    )
                )
            )

            for result in detail_results:
                yield result
            return

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

        if self.discovery_config.get(
            "strategy"
        ) == "list_only":
            for result in self.parse_list_rows(
                listing_response
            ):
                yield result
            return

        pending_details = await asyncio.to_thread(
            self._discover_from_saved_listing,
            listing_response,
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
        - pagination and natural source end

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

        page_number = start_page
        next_response = response
        browser = None

        try:
            while next_response is not None:
                outputs = super().parse_listing(
                    response=next_response,
                    page_number=page_number,
                )
                next_request = None

                for output in outputs:
                    if not isinstance(output, scrapy.Request):
                        continue

                    callback_arguments = dict(output.cb_kwargs or {})
                    if "record_key" not in callback_arguments:
                        next_request = output
                        continue

                    pending_details.append({
                        "detail_url": output.url,
                        "record_key": callback_arguments["record_key"],
                        "is_known": callback_arguments["is_known"],
                        "source_record_id": callback_arguments.get("source_record_id"),
                        "identity_fields": callback_arguments.get("identity_fields") or {},
                    })

                if next_request is None:
                    break

                if browser is None:
                    config = self.source_config.get("bypass_config", {})
                    browser = StealthBrowserEngine(
                        headless=config.get("headless", False),
                        timeoutSeconds=config.get("timeout_seconds", 90),
                    )
                    browser.__enter__()

                # Listing pages can legitimately contain no article links at
                # source end, so wait for page load rather than an article.
                if not browser.navigate(next_request.url):
                    self.discovery_service.mark_discovery_failure(
                        "LISTING_NAVIGATION_FAILED"
                    )
                    break

                if not browser.waitForPageLoad(browser.timeoutSeconds):
                    self.discovery_service.mark_discovery_failure(
                        "LISTING_PAGE_LOAD_FAILED"
                    )
                    break

                html = browser.getHtml() or ""
                if not html or any(marker in html.lower() for marker in (
                    "performing security verification",
                    "sorry, you have been blocked",
                )):
                    self.discovery_service.mark_discovery_failure(
                        "LISTING_CHALLENGE_OR_EMPTY_RESPONSE"
                    )
                    break

                next_response = HtmlResponse(
                    url=next_request.url,
                    body=html.encode("utf-8"),
                    encoding="utf-8",
                )
                title = " ".join(next_response.css("title::text").getall()).lower()
                if any(marker in title for marker in (
                    "just a moment", "verify you are human", "access denied",
                )):
                    self.discovery_service.mark_discovery_failure(
                        "LISTING_CHALLENGE_OR_EMPTY_RESPONSE"
                    )
                    break
                page_number = next_request.cb_kwargs["page_number"]
        except Exception:
            self.discovery_service.mark_discovery_failure(
                "LISTING_BROWSER_ERROR"
            )
            self.logger.exception("Saved Media listing pagination failed.")
        finally:
            if browser is not None:
                browser.__exit__(None, None, None)

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
                failed_record = self.failed_detail_record(
                    detail_url=item.get(
                        "detail_url"
                    ),
                    details=item,
                    error=item.get(
                        "fetch_error",
                        "Media detail fetch failed.",
                    ),
                    error_type=item.get(
                        "fetch_error_type",
                        "DetailFetchError",
                    ),
                )

                self.records.append(
                    failed_record
                )

                yield failed_record
                continue

            # Reuse the existing Media method.
            # This is the point where a separate
            # detail HTML file is saved.
            detail_results = super().parse_detail(
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

            for result in detail_results:
                self._download_documents(
                    result=result,
                    fetcher=fetcher,
                )

                yield result

    def _download_documents(
        self,
        result: dict,
        fetcher: BrowserDetailFetcher,
    ) -> None:
        """Download files linked from the detail page, per `document_download` config."""

        document_config = self.source_config.get(
            "document_download"
        )

        extracted = result.get("extracted")

        if not document_config or not extracted:
            return

        document_urls = []

        for field_name in document_config.get(
            "url_fields",
            [],
        ):
            values = extracted.get(field_name)

            if not isinstance(values, list):
                values = [values]

            for value in values:
                if not value:
                    continue

                document_url = urljoin(
                    extracted["SourceURL"],
                    value,
                )

                if document_url not in document_urls:
                    document_urls.append(
                        document_url
                    )

        extracted["DocumentUrls"] = document_urls
        extracted["DocumentFilePaths"] = []

        if not document_urls:
            if document_config.get("required", False):
                self._mark_document_failure(
                    result,
                    "No document URL found on the detail page.",
                )
            return

        file_name_id = self._build_file_name_id(
            record_key=result["record_key"],
        )

        for index, document_url in enumerate(
            document_urls
        ):
            suffix = PurePosixPath(
                unquote(urlparse(document_url).path)
            ).suffix.lower()

            try:
                document_path = download(
                    DownloadTask(
                        url=document_url,
                        source_name=self.task.source_name,
                        list_name=self.task.list_name,
                        download_dir=self.task.download_dir,
                        filename=f"{file_name_id}_{index}{suffix}",
                        headers=fetcher.session_headers(),
                    )
                )

            except Exception:
                self.logger.exception(
                    "Media document download failed. url=%s",
                    document_url,
                )
                self._mark_document_failure(
                    result,
                    f"Document download failed: {document_url}",
                )
                return

            extracted["DocumentFilePaths"].append(
                document_path
            )

        if document_config.get(
            "store_as_record_file",
            False,
        ):
            result["detail_file_path"] = (
                extracted["DocumentFilePaths"][0]
            )
            result["file_url"] = document_urls[0]

    @staticmethod
    def _mark_document_failure(
        result: dict,
        error: str,
    ) -> None:
        """Fail the record so it is retried on the next run instead of stored."""

        result.update(
            {
                "failed": True,
                "error": error,
                "error_stage": "DOCUMENT_FETCH",
                "error_type": "DocumentFetchError",
            }
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
