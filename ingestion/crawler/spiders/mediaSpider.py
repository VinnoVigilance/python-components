import hashlib
import re
from typing import Any

import scrapy


class MediaSpider(scrapy.Spider):
    """
    Spider used for Adverse Media sources.

    Responsibilities:
    - Discover media detail URLs.
    - Build candidate identity information.
    - Ask MediaDiscoveryService whether the record is known.
    - Track discovery stop condition through the service.
    - Download detail pages.
    - Save original detail HTML.
    - Extract source fields from the detail page.

    It does NOT:
    - Query the database directly.
    - Decide record identity formula itself.
    - Perform PreProcessing / Normalization.
    - Insert Raw or Core records.
    - Resolve external_id for Core.
    """

    name = "media_spider"

    def __init__(
        self,
        task,
        source_config,
        storage,
        records,
        discovery_service,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.task = task
        self.source_config = source_config
        self.storage = storage
        self.records = records
        self.discovery_service = discovery_service

        self.discovery_config = source_config.get(
            "discovery",
            {},
        )

        self.extraction_config = source_config.get(
            "extraction",
            {},
        )

        # Prevent duplicate listing links from affecting
        # discovery counters or creating duplicate requests.
        self.seen_record_keys: set[str] = set()

    async def start(self):
        """
        Start every discovery run from the first/newest page.
        """

        start_url = self.discovery_config.get(
            "start_url",
            self.task.url,
        )

        start_page = int(
            self.discovery_config.get(
                "start_page",
                1,
            )
        )

        yield scrapy.Request(
            url=start_url,
            callback=self.parse_listing,
            cb_kwargs={
                "page_number": start_page,
            },
            dont_filter=True,
        )

    def parse_listing(
        self,
        response,
        page_number: int,
    ):
        """
        Discover article URLs from one listing page.

        DiscoveryService decides:
        - KNOWN / NEW
        - whether discovery should stop
        """

        article_config = self.discovery_config.get(
            "article",
            {},
        )

        link_selector = article_config.get(
            "link_selector"
        )

        if not link_selector:
            raise ValueError(
                "discovery.article.link_selector "
                "is required for MediaSpider."
            )

        link_nodes = response.css(
            link_selector
        )

        if not link_nodes:
            self.logger.info(
                "No media links found on page %s",
                page_number,
            )
            return

        should_stop_discovery = False

        for link_node in link_nodes:

            href = link_node.attrib.get(
                "href"
            )

            if not href:
                continue

            detail_url = response.urljoin(
                href
            )

            source_record_id = (
                self._extract_source_record_id(
                    detail_url
                )
            )

            candidate_record = {
                "SourceURL": detail_url,
                "SourceRecordId": source_record_id,
            }

            try:
                record_key = (
                    self.discovery_service
                    .build_record_key(
                        candidate_record
                    )
                )

            except ValueError as exc:
                self.logger.warning(
                    "Could not build media identity "
                    "for URL %s: %s",
                    detail_url,
                    exc,
                )
                continue

            # Ignore duplicate links before they affect
            # KNOWN counters.
            if record_key in self.seen_record_keys:
                self.logger.debug(
                    "Duplicate media link ignored | key=%s",
                    record_key,
                )
                continue

            self.seen_record_keys.add(
                record_key
            )

            try:
                (
                    is_known,
                    should_stop,
                ) = (
                    self.discovery_service
                    .check_record_key(
                        record_key
                    )
                )

            except Exception as exc:
                self.logger.warning(
                    "Could not check media record "
                    "key=%s: %s",
                    record_key,
                    exc,
                )
                continue

            self.logger.info(
                "Media discovered | key=%s | known=%s",
                record_key,
                is_known,
            )

            # IMPORTANT:
            #
            # Even KNOWN records are opened until the
            # discovery stop threshold is reached.
            #
            # Later stages use content_hash to determine
            # whether the same logical record changed.
            yield scrapy.Request(
                url=detail_url,
                callback=self.parse_detail,
                cb_kwargs={
                    "record_key": record_key,
                    "is_known": is_known,
                    "source_record_id": source_record_id,
                },
            )

            if should_stop:
                should_stop_discovery = True

                self.logger.info(
                    "Media discovery stop threshold "
                    "reached on page %s",
                    page_number,
                )

                break

        if should_stop_discovery:
            return

        next_url = self._build_next_page_url(
            current_page=page_number,
        )

        if not next_url:
            return

        yield scrapy.Request(
            url=next_url,
            callback=self.parse_listing,
            cb_kwargs={
                "page_number": page_number + 1,
            },
        )

    def parse_detail(
        self,
        response,
        record_key: str,
        is_known: bool,
        source_record_id: str | None,
    ):
        """
        Save original HTML first, then extract fields.

        Saving HTML before extraction ensures the original
        source remains available even if extraction fails.
        """

        file_name_id = self._build_file_name_id(
            record_key=record_key,
            source_record_id=source_record_id,
        )

        # Save the original HTML before any parsing/extraction.
        detail_file_path = (
            self.storage.save_detail_html(
                record_id=file_name_id,
                content=response.text,
            )
        )

        extracted_record = (
            self._extract_record(
                response
            )
        )

        # Ensure discovery and detail extraction use
        # the same canonical URL.
        extracted_record["SourceURL"] = (
            response.url
        )

        if source_record_id:
            extracted_record[
                "SourceRecordId"
            ] = source_record_id

        result = {
            "record_key": record_key,
            "is_known": is_known,
            "detail_file_path": detail_file_path,
            "extracted": extracted_record,
        }

        self.records.append(
            result
        )

        yield result

    def _extract_record(
        self,
        response,
    ) -> dict[str, Any]:
        """
        Extract configured fields from the detail page.

        Example:

        {
            "SourceURL": "...",
            "SourceRecordId": "10914",
            "Title": "...",
            "OriginalValue": "...",
            "AuthorName": "...",
            "BodyText": "..."
        }
        """

        record: dict[str, Any] = {}

        for (
            field_name,
            field_config,
        ) in self.extraction_config.items():

            record[field_name] = (
                self._extract_field(
                    response=response,
                    field_config=field_config,
                )
            )

        return record

    def _extract_field(
        self,
        response,
        field_config: dict[str, Any],
    ):
        """
        Extract one configured field.

        Supported:

        source: response_url

        selector: ".entry-content"
        output: text

        selector: "a"
        output: attribute
        attribute: href

        Optional:

        extraction:
            strategy: regex
            pattern: "..."
        """

        source = field_config.get(
            "source"
        )

        if source == "response_url":
            value = response.url

        else:
            selector = field_config.get(
                "selector"
            )

            if not selector:
                return None

            output = str(
                field_config.get(
                    "output",
                    "text",
                )
            ).strip().lower()

            selected_nodes = response.css(
                selector
            )

            if not selected_nodes:
                return None

            if output == "text":

                texts = selected_nodes.xpath(
                    ".//text()"
                ).getall()

                value = self._clean_text(
                    " ".join(texts)
                )

            elif output == "html":

                value = selected_nodes.get()

            elif output == "attribute":

                attribute = field_config.get(
                    "attribute"
                )

                if not attribute:
                    raise ValueError(
                        "attribute is required "
                        "when output=attribute"
                    )

                value = selected_nodes.attrib.get(
                    attribute
                )

                if (
                    value
                    and attribute
                    in {"href", "src"}
                ):
                    value = response.urljoin(
                        value
                    )

            else:
                raise ValueError(
                    f"Unsupported extraction output: "
                    f"{output}"
                )

        extraction = field_config.get(
            "extraction"
        )

        if extraction:
            value = self._apply_extraction(
                value=value,
                extraction=extraction,
            )

        return value

    def _extract_source_record_id(
        self,
        detail_url: str,
    ) -> str | None:
        """
        Extract SourceRecordId during discovery.

        Example NBI:

            https://nbi.gov.ph/.../10914/

        becomes:

            10914
        """

        field_config = self.extraction_config.get(
            "SourceRecordId",
            {},
        )

        source = field_config.get(
            "source"
        )

        if source != "response_url":
            return None

        extraction = field_config.get(
            "extraction"
        )

        # Backward-compatible shorter format:
        #
        # strategy: regex
        # pattern: ...
        if extraction is None:

            strategy = field_config.get(
                "strategy"
            )

            pattern = field_config.get(
                "pattern"
            )

            if strategy or pattern:
                extraction = {
                    "strategy": strategy,
                    "pattern": pattern,
                }

        if not extraction:
            return None

        return self._apply_extraction(
            value=detail_url,
            extraction=extraction,
        )

    @staticmethod
    def _apply_extraction(
        value: str | None,
        extraction: dict[str, Any],
    ):
        """
        Apply extraction strategy to a value.

        Currently supported:
        - regex
        """

        if value is None:
            return None

        strategy = str(
            extraction.get(
                "strategy",
                "",
            )
        ).strip().lower()

        if strategy == "regex":

            pattern = extraction.get(
                "pattern"
            )

            if not pattern:
                raise ValueError(
                    "Regex extraction requires pattern."
                )

            match = re.search(
                pattern,
                str(value),
            )

            if not match:
                return None

            if match.lastindex:
                return (
                    match.group(1)
                    .strip()
                )

            return (
                match.group(0)
                .strip()
            )

        raise ValueError(
            f"Unsupported extraction strategy: "
            f"{strategy}"
        )

    def _build_next_page_url(
        self,
        current_page: int,
    ) -> str | None:
        """
        Build next listing page URL.

        Currently supports:
        - none
        - page_number
        """

        pagination = self.discovery_config.get(
            "pagination",
            {},
        )

        pagination_type = str(
            pagination.get(
                "type",
                "none",
            )
        ).strip().lower()

        if pagination_type == "none":
            return None

        if pagination_type != "page_number":
            raise ValueError(
                "MediaSpider currently supports "
                "page_number pagination only."
            )

        url_pattern = pagination.get(
            "url_pattern"
        )

        if not url_pattern:
            raise ValueError(
                "pagination.url_pattern is required "
                "for page_number pagination."
            )

        return url_pattern.format(
            page=current_page + 1
        )

    @staticmethod
    def _build_file_name_id(
        record_key: str,
        source_record_id: str | None,
    ) -> str:
        """
        Build a safe local HTML filename.

        If source provides an ID:
            10914.html

        If not:
            use a short hash only for filename generation.

        record_key itself remains human-readable.
        """

        if source_record_id:
            return str(
                source_record_id
            ).strip()

        return hashlib.sha256(
            record_key.encode(
                "utf-8"
            )
        ).hexdigest()[:24]

    @staticmethod
    def _clean_text(
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        cleaned = " ".join(
            value.split()
        ).strip()

        return cleaned or None