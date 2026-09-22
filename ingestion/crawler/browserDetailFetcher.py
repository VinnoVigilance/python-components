import logging

from collections.abc import (
    Callable,
    Iterator,
)
from pathlib import Path
from typing import Any

from scrapy.http import HtmlResponse

from ingestion.bypassCollector.engines.stealthBrowserEngine import (
    StealthBrowserEngine,
)


logger = logging.getLogger(__name__)


class BrowserDetailFetcher:
    """
    Fetch detail pages sequentially using one
    stealth-browser session.

    Responsibilities:

    - Reuse an existing saved detail page.
    - Open one browser for all missing detail pages.
    - Navigate to each detail URL sequentially.
    - Wait for the configured selector.
    - Return an HtmlResponse.

    This class does not:

    - Extract source fields.
    - Build Watchlist or Media records.
    - Save records in the database.
    - Decide the output record structure.
    """

    def __init__(
        self,
        browser_config: dict[str, Any],
        storage_config: dict[str, Any] | None = None,
        cache_path_builder: (
            Callable[
                [dict[str, Any]],
                Path | None,
            ]
            | None
        ) = None,
        engine_factory: Callable[
            ...,
            Any,
        ] = StealthBrowserEngine,
        component_logger=None,
    ):
        self.browser_config = (
            browser_config or {}
        )

        self.storage_config = (
            storage_config or {}
        )

        self.cache_path_builder = (
            cache_path_builder
        )

        self.engine_factory = (
            engine_factory
        )

        self.logger = (
            component_logger
            or logger
        )

    def fetch(
        self,
        pending_details: list[
            dict[str, Any]
        ],
    ) -> Iterator[
        tuple[
            dict[str, Any],
            HtmlResponse,
        ]
    ]:
        """
        Yield each original detail item together
        with its loaded HtmlResponse.
        """

        timeout_seconds = int(
            self.browser_config.get(
                "timeout_seconds",
                90,
            )
        )

        wait_selector = (
            self.browser_config.get(
                "wait_selector"
            )
        )

        reuse_saved_pages = bool(
            self.storage_config.get(
                "reuse_saved_detail_pages",
                True,
            )
        )

        minimum_saved_size = int(
            self.storage_config.get(
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
                detail_url = item.get(
                    "detail_url"
                )

                if not detail_url:
                    raise ValueError(
                        "detail_url is missing from "
                        "browser detail item."
                    )

                self.logger.info(
                    "Processing browser detail "
                    "%s/%s: %s",
                    index,
                    len(pending_details),
                    detail_url,
                )

                cached_response = (
                    self._load_cached_response(
                        item=item,
                        detail_url=detail_url,
                        reuse_saved_pages=(
                            reuse_saved_pages
                        ),
                        minimum_saved_size=(
                            minimum_saved_size
                        ),
                    )
                )

                if cached_response is not None:
                    yield (
                        item,
                        cached_response,
                    )

                    continue

                if engine is None:
                    self.logger.info(
                        "Starting detail browser."
                    )

                    engine = self.engine_factory(
                        headless=(
                            self.browser_config.get(
                                "headless",
                                False,
                            )
                        ),
                        successCriteria=(
                            self.browser_config.get(
                                "success_criteria",
                                [],
                            )
                        ),
                        timeoutSeconds=(
                            timeout_seconds
                        ),
                        driverVersion=(
                            self.browser_config.get(
                                "driver_version",
                                "mlatest",
                            )
                        ),
                        binaryLocation=(
                            self.browser_config.get(
                                "binary_location"
                            )
                        ),
                    )

                    engine.__enter__()

                if not engine.navigate(
                    detail_url
                ):
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
                        "Detail page did not become "
                        f"ready: {detail_url}"
                    )

                html = engine.getHtml()

                if not html:
                    raise RuntimeError(
                        "Detail page returned empty "
                        f"HTML: {detail_url}"
                    )

                detail_response = HtmlResponse(
                    url=detail_url,
                    body=html.encode(
                        "utf-8"
                    ),
                    encoding="utf-8",
                )

                yield (
                    item,
                    detail_response,
                )

        finally:
            if engine is not None:
                engine.__exit__(
                    None,
                    None,
                    None,
                )

    def _load_cached_response(
        self,
        item: dict[str, Any],
        detail_url: str,
        reuse_saved_pages: bool,
        minimum_saved_size: int,
    ) -> HtmlResponse | None:
        """
        Return an HtmlResponse from an existing
        valid file, when cache reuse is enabled.
        """

        if not reuse_saved_pages:
            return None

        if self.cache_path_builder is None:
            return None

        cached_path = (
            self.cache_path_builder(
                item
            )
        )

        if cached_path is None:
            return None

        cached_path = Path(
            cached_path
        )

        if not cached_path.is_file():
            return None

        if (
            cached_path.stat().st_size
            < minimum_saved_size
        ):
            return None

        self.logger.info(
            "Reusing saved detail page: %s",
            cached_path,
        )

        return HtmlResponse(
            url=detail_url,
            body=cached_path.read_bytes(),
            encoding="utf-8",
        )