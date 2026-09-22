import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import scrapy
from scrapy.http import HtmlResponse

from ingestion.bypassCollector.engines.stealthBrowserEngine import (
    StealthBrowserEngine,
)
from ingestion.crawler.spiders.genericSpider import GenericSpider
from ingestion.crawler.browserDetailFetcher import (
    BrowserDetailFetcher,
)


_ITERATION_FINISHED = object()


def _get_next_result(iterator):
    """
    Read one result from the synchronous browser generator.

    A sentinel is returned when iteration finishes because
    StopIteration cannot safely enter an asyncio Future.
    """

    try:
        return next(iterator)

    except StopIteration:
        return _ITERATION_FINISHED


class SavedHtmlSpider(GenericSpider):
    """
    Parse a previously saved listing HTML.

    Detail pages can be fetched directly with Scrapy or
    through the configured browser engine.
    """

    name = "saved_html_spider"

    async def start(self):
        if not self.task.source_file_path:
            raise ValueError(
                "source_file_path is required "
                "for saved_html strategy"
            )

        source_file = Path(
            self.task.source_file_path
        ).resolve()

        if not source_file.is_file():
            raise FileNotFoundError(
                f"Saved HTML was not found: {source_file}"
            )

        response = HtmlResponse(
            url=self.task.url,
            body=source_file.read_bytes(),
            encoding="utf-8",
        )

        detail_fetch_strategy = str(
            self.config.get(
                "detail_fetch_strategy",
                "direct",
            )
        ).strip().lower()

        if detail_fetch_strategy == "browser":
            iterator = self.parse(response)
            loop = asyncio.get_running_loop()

            with ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=(
                    "saved-html-browser"
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

            return

        for result in self.parse(response):
            yield result

    def parse(self, response):
        self.current_url = response.url

        discovery = self.config.get(
            "discovery",
            {},
        )

        row_selector = discovery.get(
            "row_selector"
        )

        if not row_selector:
            raise ValueError(
                "row_selector is required"
            )

        record_mode = str(
            self.config.get(
                "record_mode",
                "listing_only",
            )
        ).strip().lower()

        if record_mode not in {
            "listing_only",
            "list_detail",
        }:
            raise ValueError(
                f"Unsupported record_mode: {record_mode}"
            )

        rows = self._select_nodes(
            response,
            selector=row_selector,
            selector_type=discovery.get(
                "selector_type",
                "css",
            ),
        )

        self.logger.info(
            "Listing rows discovered before "
            "deduplication: %s",
            len(rows),
        )

        pending_details = []
        seen_record_ids = set()

        for row in rows:
            list_data = self._extract_fields(
                row,
                self.config.get(
                    "list_fields",
                    {},
                ),
            )

            detail_url = (
                self._extract_saved_detail_url(
                    row=row,
                    response=response,
                )
            )

            if record_mode == "listing_only":
                record_id = (
                    self._extract_record_id(detail_url, list_data)
                    if detail_url
                    else None
                )

                if record_id is not None:
                    if record_id in seen_record_ids:
                        self.logger.debug(
                            "Skipping duplicate record: %s",
                            record_id,
                        )
                        continue

                    seen_record_ids.add(record_id)

                record = {
                    "source_record_id": record_id,
                    "list": list_data,
                }

                if detail_url:
                    record["detail_url"] = detail_url

                self.records.append(record)
                yield record
                continue

            if not detail_url:
                continue

            record_id = self._extract_record_id(
                detail_url, list_data
            )

            if not record_id:
                continue

            if record_id in seen_record_ids:
                self.logger.debug(
                    "Skipping duplicate record: %s",
                    record_id,
                )
                continue

            seen_record_ids.add(record_id)

            pending_details.append(
                {
                    "list_data": list_data,
                    "record_id": record_id,
                    "detail_url": detail_url,
                }
            )

        if record_mode != "list_detail":
            return

        self.logger.info(
            "Unique detail pages queued: %s",
            len(pending_details),
        )

        if not pending_details:
            raise ValueError(
                "No detail pages were discovered "
                "from saved listing HTML."
            )

        detail_fetch_strategy = str(
            self.config.get(
                "detail_fetch_strategy",
                "direct",
            )
        ).strip().lower()

        if detail_fetch_strategy == "direct":
            for item in pending_details:
                yield scrapy.Request(
                    url=item["detail_url"],
                    callback=self.parse_detail,
                    cb_kwargs=item,
                )

            return

        if detail_fetch_strategy == "browser":
            yield from self._fetch_browser_details(
                pending_details
            )
            return

        raise ValueError(
            "Unsupported detail_fetch_strategy: "
            f"{detail_fetch_strategy}"
        )

    def _fetch_browser_details(
        self,
        pending_details,
    ):
        """
        Fetch Watchlist detail pages through the
        shared browser fetcher.

        Watchlist record extraction and output format
        remain unchanged.
        """

        if self.storage is None:
            raise ValueError(
                "Crawler storage is required "
                "for browser detail pages."
            )

        browser_config = self.config.get(
            "detail_browser",
            {},
        )

        storage_config = self.config.get(
            "storage",
            {},
        )

        fetcher = BrowserDetailFetcher(
            browser_config=browser_config,
            storage_config=storage_config,

            # Preserve the existing Watchlist
            # attachments/members/{record_id}.html
            # cache location.
            cache_path_builder=(
                lambda item: (
                    self.storage.detail_path
                    / f"{item['record_id']}.html"
                )
            ),

            # Keep this injected so the existing
            # Watchlist regression tests continue
            # patching the same engine.
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
            # GenericSpider.parse_detail keeps the exact
            # existing Watchlist output contract:
            #
            # source_record_id
            # list
            # detail
            # attachments
            yield from super().parse_detail(
                response=detail_response,
                **item,
            )


    def _extract_saved_detail_url(
        self,
        row,
        response,
    ):
        discovery = self.config.get(
            "discovery",
            {},
        )

        selector = discovery.get(
            "detail_link_selector"
        )

        if not selector:
            return None

        selected = self._select_nodes(
            row,
            selector=selector,
            selector_type=discovery.get(
                "detail_link_selector_type",
                "css",
            ),
        )

        attribute = discovery.get(
            "detail_link_attribute",
            "href",
        )

        href = (
            selected.attrib.get(attribute)
            if selected
            else None
        )

        return (
            response.urljoin(href)
            if href
            else None
        )

    @staticmethod
    def _select_nodes(
        node,
        selector,
        selector_type="css",
    ):
        selector_type = str(
            selector_type
        ).strip().lower()

        if selector_type == "xpath":
            return node.xpath(selector)

        if selector_type == "css":
            return node.css(selector)

        raise ValueError(
            f"Unsupported selector_type: {selector_type}"
        )