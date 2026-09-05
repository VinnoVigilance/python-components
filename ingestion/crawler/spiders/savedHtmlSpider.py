import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import scrapy
from scrapy.http import HtmlResponse

from ingestion.bypassCollector.engines.stealthBrowserEngine import (
    StealthBrowserEngine,
)
from ingestion.crawler.spiders.genericSpider import GenericSpider


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

            if not detail_url:
                continue

            record_id = self._extract_record_id(
                detail_url
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

            if record_mode == "listing_only":
                record = {
                    "source_record_id": record_id,
                    "list": list_data,
                    "detail_url": detail_url,
                }

                self.records.append(record)
                yield record
                continue

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

        wait_selector = browser_config.get(
            "wait_selector"
        )

        timeout_seconds = int(
            browser_config.get(
                "timeout_seconds",
                90,
            )
        )

        reuse_saved_pages = bool(
            storage_config.get(
                "reuse_saved_detail_pages",
                True,
            )
        )

        minimum_saved_size = int(
            storage_config.get(
                "minimum_detail_size_bytes",
                1000,
            )
        )

        engine = None

        try:
            for index, item in enumerate(
                pending_details,
                start=1,
            ):
                detail_url = item["detail_url"]
                record_id = item["record_id"]

                detail_file_path = (
                    self.storage.detail_path
                    / f"{record_id}.html"
                )

                self.logger.info(
                    "Processing unique detail page "
                    "%s/%s: %s",
                    index,
                    len(pending_details),
                    detail_url,
                )

                if (
                    reuse_saved_pages
                    and detail_file_path.is_file()
                    and detail_file_path.stat().st_size
                    >= minimum_saved_size
                ):
                    self.logger.info(
                        "Reusing saved detail page: %s",
                        detail_file_path,
                    )

                    detail_response = HtmlResponse(
                        url=detail_url,
                        body=(
                            detail_file_path.read_bytes()
                        ),
                        encoding="utf-8",
                    )

                    yield from super().parse_detail(
                        response=detail_response,
                        **item,
                    )

                    continue

                if engine is None:
                    self.logger.info(
                        "Starting detail browser."
                    )

                    engine = StealthBrowserEngine(
                        headless=browser_config.get(
                            "headless",
                            False,
                        ),
                        successCriteria=(
                            browser_config.get(
                                "success_criteria",
                                [],
                            )
                        ),
                        timeoutSeconds=timeout_seconds,
                    )

                    engine.__enter__()

                if not engine.navigate(detail_url):
                    raise RuntimeError(
                        "Could not open detail page: "
                        f"{detail_url}"
                    )

                if (
                    wait_selector
                    and not engine.waitForElement(
                        wait_selector,
                        timeout_seconds,
                    )
                ):
                    raise RuntimeError(
                        "Detail page did not "
                        "become ready: "
                        f"{detail_url}"
                    )

                html = engine.getHtml()

                if not html:
                    raise RuntimeError(
                        "Detail page returned "
                        "empty HTML: "
                        f"{detail_url}"
                    )

                detail_response = HtmlResponse(
                    url=detail_url,
                    body=html.encode("utf-8"),
                    encoding="utf-8",
                )

                yield from super().parse_detail(
                    response=detail_response,
                    **item,
                )

        finally:
            if engine is not None:
                engine.__exit__(
                    None,
                    None,
                    None,
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