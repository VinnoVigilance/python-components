import hashlib
import itertools
import json
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from xml.etree import ElementTree

import scrapy

from scrapy.http import HtmlResponse
from ingestion.apiCollector.pagination import build_query, read_path
from ingestion.crawler.pagination import resolve_next_page_url
from services.adverseMediaPipeline import mediaTodoService
from services.adverseMediaPipeline.mediaIdentityService import MediaIdentityService


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
        self.persist_pending_work = getattr(discovery_service, "persist_pending_work", True)
        self.storage = storage
        self.records = records
        self.discovery_service = discovery_service

        self.discovery_config = source_config.get(
            "discovery",
            {},
        )

        self.discovery_policy = str(
            self.discovery_config.get(
                "policy",
                "full_scan",
            )
        ).strip().lower()

        self.extraction_config = source_config.get(
            "extraction",
            {},
        )

        # Prevent duplicate listing links from affecting
        # discovery counters or creating duplicate requests.
        self.seen_record_keys: set[str] = set()
        self.form_publications: dict[str, dict[str, Any]] = {}
        self.form_document_references: dict[str, list[dict[str, Any]]] = {}

    async def start(self):
        """
        Start every discovery run from the first/newest page.
        """

        detail_items = getattr(self.task, "detail_items", None) or []
        discovery_items = getattr(self.task, "discovery_items", None) or []
        if (not detail_items and not discovery_items and
                self.discovery_config.get("strategy") == "form_search"):
            await self.collect_form_searches()
            self.records[:] = list({record["record_key"]: record for record in self.records}.values())
            return
        if detail_items or discovery_items:
            for item in detail_items:
                yield scrapy.Request(
                    url=item["detail_url"],
                    callback=self.parse_detail,
                    errback=self.detail_failed,
                    cb_kwargs=self.detail_item_kwargs(item),
                    dont_filter=True,
                )
            for item in discovery_items:
                if item.get("kind") == "form_search":
                    await self.collect_form_searches([item])
                    continue
                index_url = item["index_file_url"]
                start_url = self.discovery_config.get("start_url", self.task.url)
                if (urlparse(index_url).scheme, urlparse(index_url).netloc) != (urlparse(start_url).scheme, urlparse(start_url).netloc):
                    yield self._failed_json_entry_retry(item, "Index URL is not on the source host")
                    continue
                yield scrapy.Request(
                    url=index_url, callback=(self.retry_month_index if item.get("kind") == "month_index" else self.retry_json_entry),
                    errback=self.retry_json_entry_failed,
                    cb_kwargs={"retry_item": item}, dont_filter=True,
                )
            return

        start_url = self.discovery_config.get(
            "start_url",
            self.task.url,
        )

        if self.discovery_config.get("strategy") == "listing_groups":
            self.group_known_stop = getattr(self.discovery_service, "stop_after_known", False)
            self.group_stopped_early = False
            groups = self.discovery_config["groups"]
            self.parallel_html_listing_groups = bool(
                self.discovery_config.get("parallel_groups")
                and self.discovery_policy == "full_scan"
                and groups
                and all("link_selector" in group for group in groups)
            )
            if self.parallel_html_listing_groups:
                self.html_group_states = {}
                self.completed_html_groups = set()
                for index in range(len(groups)):
                    yield self._html_listing_group_request(index)
                return
            for request in self._start_listing_group(0):
                yield request
            return

        if self.discovery_config.get("strategy") == "json_manifest":
            yield scrapy.Request(
                url=start_url,
                callback=self.parse_json_manifest,
                errback=self.json_discovery_failed,
                dont_filter=True,
            )
            return
        if self.discovery_config.get("strategy") == "archive_listings":
            urls = self.discovery_config.get("rendered_article_urls")
            if urls is None:
                self.discovery_service.mark_discovery_failure(
                    "ARCHIVE_LINKS_NOT_COLLECTED"
                )
                return

            for url in urls:
                request, _ = self._discover_detail(url)
                if request is not None:
                    yield request

            if self.discovery_config.get("archive_collection_complete") is True:
                self.discovery_service.mark_source_end()
            else:
                self.discovery_service.mark_discovery_failure(
                    "ARCHIVE_COLLECTION_INCOMPLETE"
                )
            return
        
        if self.discovery_config.get("strategy") == "rss":
            feeds = list(dict.fromkeys(self.discovery_config.get("feed_urls", [])))
            if not feeds:
                self.discovery_service.mark_discovery_failure("MISSING_RSS_FEEDS")
                return

            self.rss_pagination = self.discovery_config.get("pagination")
            if self.rss_pagination and (
                self.rss_pagination.get("type") != "offset" or len(feeds) != 1
            ):
                raise ValueError("Paginated RSS requires offset pagination and one feed URL")
            self.pending_rss_feeds = len(feeds)
            self.rss_seen_pages = set()
            self.rss_last_date = None
            for url in feeds:
                yield scrapy.Request(
                    self._rss_page_url(url, 0) if self.rss_pagination else url,
                    callback=self.parse_rss,
                    errback=self.rss_failed,
                    cb_kwargs={"feed_url": url, "offset": 0},
                )
            return

        if self.discovery_config.get("strategy") == "dated_month_archive":
            yield scrapy.Request(start_url, callback=self.parse_month_archive,
                                 errback=self.json_discovery_failed, dont_filter=True)
            return

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

    async def collect_form_searches(self, retry_items=None, *, settings=None, replay_pending=True):
        """Run bounded, sequential HTML form searches through Scrapy's downloader.

        Each retry carries a declarative recipe, never cookies, row positions or
        hidden form values. Parents are reopened before following another child.
        Production retains pending work; no-DB discovery disables that persistence.
        Search progress is not saved.
        """
        if settings is None:
            settings = self.discovery_config.get("form_search", {})
        if retry_items is None and isinstance(settings, list):
            if not settings or any(not isinstance(recipe, dict) for recipe in settings):
                raise ValueError("form_search must be a recipe or a non-empty list of recipes")
            for index, recipe in enumerate(settings):
                await self.collect_form_searches(settings=recipe, replay_pending=index == 0)
                if getattr(self.discovery_service, "limit_reached", False):
                    return
            if self.discovery_service and any(recipe.get("parameters") for recipe in settings):
                self.discovery_service.reached_source_end = False
                if self.discovery_service.discovery_failure_count == 0:
                    self.discovery_service.stop_reason = "CONFIGURED_RANGE_COMPLETE"
            return
        dataset = self.source_config["dataset_name"]
        if retry_items is None:
            stages = settings.get("stages", [])
            if not stages:
                raise ValueError("form_search.stages must describe at least one document listing")
            names, variants = [], []
            for name, spec in settings.get("parameters", {}).items():
                names.append(name)
                if "values" in spec:
                    values = spec["values"]
                    if not values:
                        raise ValueError(f"Search parameter {name} has no values")
                    variants.append(values)
                else:
                    bounds = spec.get("range", {})
                    first, last = bounds.get("start"), bounds.get("end")
                    if type(first) is not int or type(last) is not int or not 0 <= first <= last:
                        raise ValueError(f"Search parameter {name} needs an explicit inclusive range")
                    variants.append(range(first, last + 1))
            recipes = (dict(zip(names, values)) for values in itertools.product(*variants))
            work = ({"kind": "form_search", "recipe": settings,
                             "parameters": {name: format(value, settings["parameters"][name].get("format", ""))
                                            for name, value in parameters.items()}}
                    for parameters in recipes)
        else:
            work = retry_items
        if retry_items is None and replay_pending and self.persist_pending_work:
            pending = mediaTodoService.load_items(dataset)
            active_recipes = self.discovery_config.get("form_search", {})
            if not isinstance(active_recipes, list):
                active_recipes = [active_recipes]
            # Older pending snapshots may contain the removed checkpoint setting.
            active_recipes = [{key: value for key, value in recipe.items() if key != "checkpoint"}
                              for recipe in active_recipes]
            replay = [entry for entry in pending if entry.get("kind") == "form_search"
                      and {key: value for key, value in entry.get("recipe", {}).items()
                           if key != "checkpoint"} in active_recipes]
            for entry in pending:
                if entry.get("kind") == "process":
                    if Path(entry["detail_file_path"]).is_file():
                        self.records.append({key: value for key, value in entry.items() if key != "kind"})
                    elif (entry.get("discovery_context")
                          and {key: value for key, value in entry["discovery_context"].get("recipe", {}).items()
                               if key != "checkpoint"} in active_recipes):
                        replay.append(entry["discovery_context"])
            if replay:
                unique = {entry["record_key"]: entry for entry in replay}
                await self.collect_form_searches(list(unique.values()))
        for item in work:
            if getattr(self.discovery_service, "limit_reached", False):
                return
            fingerprint = json.dumps([item["recipe"], item["parameters"]], sort_keys=True)
            item = {**item, "record_key": item.get("record_key") or
                    "form:" + hashlib.sha256(fingerprint.encode()).hexdigest()}
            # Also covers an interrupted search before any publication is saved.
            if self.persist_pending_work:
                mediaTodoService.queue_items(dataset, [item])
            try:
                page = await self._open_form_path(item, [])
                inherited = {field: item["parameters"][parameter]
                             for field, parameter in item["recipe"].get("parameter_fields", {}).items()}
                await self._walk_form_rows(page, item, [], inherited)
                if getattr(self.discovery_service, "limit_reached", False):
                    # Keep a partially visited query pending for replay.
                    return
                if self.persist_pending_work:
                    mediaTodoService.remove_items(dataset, [item["record_key"]])
            except Exception as error:
                self.logger.exception("Form discovery failed%s",
                                      "; query remains pending" if self.persist_pending_work else "")
                if self.discovery_service:
                    self.discovery_service.mark_discovery_failure("FORM_SEARCH_INCOMPLETE")
                record = self.failed_detail_record(
                    detail_url=item["recipe"]["start_url"], details={"record_key": item["record_key"]},
                    error=str(error), error_type=type(error).__name__, error_stage="FORM_DISCOVERY")
                record["discovery_retry_item"] = item
                self.records.append(record)
        if retry_items is None and settings.get("parameters"):
            if self.discovery_service and self.discovery_service.discovery_failure_count == 0:
                self.discovery_service.stop_reason = "CONFIGURED_RANGE_COMPLETE"
        elif retry_items is None and self.discovery_service:
            self.discovery_service.mark_source_end()

    async def _download_form_request(self, request, item):
        origin = urlparse(item["recipe"]["start_url"])
        target = urlparse(request.url)
        if (origin.scheme, origin.netloc) != (target.scheme, target.netloc):
            raise ValueError("Form navigation left the configured origin")
        # Searches are sequential; one jar avoids retaining one jar per number
        # throughout a large historical range. Paths are replayed before use.
        request.meta.update(cookiejar="form_search", download_timeout=self.task.timeout)
        response = await self.crawler.engine.download_async(request)
        final = urlparse(response.url)
        if (final.scheme, final.netloc) != (origin.scheme, origin.netloc) or response.status >= 400:
            raise ValueError(f"Unexpected form response: {response.status} {response.url}")
        return response

    def _form_action(self, response, row, action, parameters=None):
        """Build a link or form action from fresh HTML, not JavaScript execution."""
        if action.get("type", "form") not in {"link", "form"}:
            raise ValueError("Navigation action must be link or form")
        if action.get("type", "form") == "link":
            links = (row.xpath(action["selector"]) if action.get("selector_type") == "xpath"
                     else row.css(action["selector"]))
            href = links[0].attrib.get("href") if links else None
            if not href:
                raise ValueError("Configured navigation link missing")
            url = response.urljoin(href)
            target, origin = urlparse(url), urlparse(response.url)
            if (action.get("upgrade_https") and origin.scheme == "https"
                    and target.scheme == "http" and target.netloc == origin.netloc):
                url = target._replace(scheme="https").geturl()
            return scrapy.Request(url, dont_filter=True)
        fields = {key: str(value).format(**(parameters or {}))
                  for key, value in action.get("fields", {}).items()}
        control = None
        if action.get("submit_selector"):
            controls = row.css(action["submit_selector"])
            if not controls:
                raise ValueError("Configured form action missing")
            control = controls[0]
            name = control.attrib.get("name") or control.attrib.get("id")
            if not name:
                raise ValueError("Form action has no name or id")
            fields[name] = action.get("submit_value", name)
        forms = ((control if control is not None else row)
                 .xpath(action.get("form_xpath", "ancestor-or-self::form[1]")))
        if len(forms) != 1:
            raise ValueError("Configured form must match exactly once")
        form = forms[0]
        form_path = form.root.getroottree().getpath(form.root)
        return scrapy.FormRequest.from_response(response, formxpath=form_path, formdata=fields,
                                                dont_click=True, dont_filter=True)

    def _form_rows(self, response, stage):
        if not isinstance(response, HtmlResponse):
            raise ValueError("Expected HTML before the publication download")
        select = response.xpath if stage.get("selector_type") == "xpath" else response.css
        if stage.get("unsupported_pagination_selector") and select(stage["unsupported_pagination_selector"]):
            raise ValueError("Untraversed server pagination detected")
        rows = select(stage["row_selector"])
        empty = stage.get("empty_pattern")
        empty_content = (response.text if stage.get("empty_source") == "html" else
                         self._clean_text(" ".join(response.xpath('//body//text()[not(ancestor::script) and not(ancestor::style)]').getall())))
        if empty and re.search(empty, empty_content, re.I):
            return []
        if not rows:
            self.storage.save_source_html(response.text)
            raise ValueError("Expected result rows or an explicit no-results notice")
        return rows

    def _form_row_key(self, metadata, stage):
        values = [metadata.get(name) for name in stage["row_key"]]
        if any(value is None or value == "" for value in values):
            raise ValueError("Missing stable row identity; refusing positional navigation")
        return values

    async def _open_form_path(self, item, path):
        recipe = item["recipe"]
        response = await self._download_form_request(
            scrapy.Request(recipe["start_url"], dont_filter=True), item)
        if recipe.get("search_action"):
            response = await self._download_form_request(
                self._form_action(response, response, recipe["search_action"], item["parameters"]), item)
        for depth, entry in enumerate(path):
            stage = recipe["stages"][depth]
            # GET pagination is independent of form state. Reopen the parent
            # first, then restore the page on which this row was discovered.
            page_url = entry.get("page_url")
            if page_url and page_url != response.url:
                url = resolve_next_page_url(response.url, page_url)
                response = await self._download_form_request(
                    scrapy.Request(url, dont_filter=True), item)
            key = entry["row_key"]
            matches = [row for row in self._form_rows(response, stage)
                       if self._form_row_key(self._extract_record(
                           row, fields=stage.get("fields", {}), base_url=response.url), stage) == key]
            if len(matches) != 1:
                raise ValueError("Previously discovered row cannot be reopened uniquely")
            response = await self._download_form_request(
                self._form_action(response, matches[0], stage["action"]), item)
        return response

    async def _walk_form_rows(self, response, item, path, inherited):
        """Walk each listing page; pagination uses existing same-origin checks."""
        stage = item["recipe"]["stages"][len(path)]
        pagination = stage.get("pagination")
        if pagination and pagination.get("type", "next_link") != "next_link":
            raise ValueError("Navigation stages support next-link pagination only")
        visited = set()
        failures = []
        while True:
            visited.add(response.url)
            try:
                await self._walk_form_page(response, item, path, inherited)
            except Exception as error:
                # Preserve successful siblings/pages, but leave the search pending.
                failures.append(str(error))
            if getattr(self.discovery_service, "limit_reached", False):
                return
            if not pagination:
                break
            select = (response.xpath if pagination.get("selector_type", "css") == "xpath"
                      else response.css)
            links = select(pagination["selector"])
            if len(links) > 1:
                raise ValueError("Next-page selector must match at most one link")
            if not links:
                break
            url = resolve_next_page_url(response.url, links[0].attrib.get("href", ""),
                                        visited_urls=visited)
            response = await self._download_form_request(
                scrapy.Request(url, dont_filter=True), item)
            if response.url in visited:
                raise ValueError("Next-page redirect returned to a visited page")
        if failures:
            raise ValueError("; ".join(failures))

    async def _walk_form_page(self, response, item, path, inherited):
        stage = item["recipe"]["stages"][len(path)]
        metadata = {**inherited, **self._extract_record(
            response, fields=stage.get("page_fields", {}), base_url=response.url)}
        rows = self._form_rows(response, stage)
        failures = []
        children = []
        for index, row in enumerate(rows, 1):
            try:
                fields = self._extract_record(
                    row, fields=stage.get("fields", {}), base_url=response.url)
                self._form_row_key(fields, stage)
                children.append((fields, row))
            except Exception as error:
                # Bad source rows must not prevent valid siblings from being saved.
                # Leave the recipe pending; the row number is diagnostic, not identity.
                failures.append(f"Row {index} at {response.url}: {error}")
                self.logger.warning("Publication row deferred: %s", failures[-1])
        # Explicit formats keep ambiguous source dates independent of locale.
        # Invalid/missing values fail discovery rather than changing run order.
        for order in reversed(stage.get("order_by", [])):
            def sort_key(child):
                value = child[0][order["field"]]
                kind = order.get("type", "text")
                if kind == "integer":
                    return int(value)
                if kind == "date":
                    return datetime.strptime(value, order["format"])
                if kind != "text":
                    raise ValueError(f"Unsupported row ordering type: {kind}")
                return str(value)
            children.sort(key=sort_key, reverse=order.get("descending", False))
        if stage.get("allow_identical_rows") and item["recipe"].get("replay_parents", True):
            raise ValueError("allow_identical_rows requires replay_parents: false")
        unique_children = []
        seen_rows = {}
        for fields, row in children:
            key = json.dumps(self._form_row_key(fields, stage))
            if key in seen_rows:
                previous_fields, previous_row = seen_rows[key]
                if stage.get("allow_identical_rows") and fields == previous_fields:
                    request = self._form_action(response, row, stage["action"])
                    previous_request = self._form_action(response, previous_row, stage["action"])
                    if (request.method, request.url, request.body) == (
                            previous_request.method, previous_request.url, previous_request.body):
                        self.logger.info("Identical publication row ignored: %s at %s", key, response.url)
                        continue
                raise ValueError("Ambiguous document/case row identity")
            seen_rows[key] = (fields, row)
            unique_children.append((fields, row))
        children = unique_children
        for fields, row in children:
            if getattr(self.discovery_service, "limit_reached", False):
                return
            key = self._form_row_key(fields, stage)
            child_path = [*path, {"row_key": key,
                                  "page_url": response.url if stage.get("pagination") else None}]
            # Replay parents for every sibling to restore server selection state.
            try:
                if item["recipe"].get("replay_parents", True):
                    child = await self._open_form_path(item, child_path)
                else:
                    child = await self._download_form_request(
                        self._form_action(response, row, stage["action"]), item)
                extracted = {**metadata, **fields}
                if len(child_path) < len(item["recipe"]["stages"]):
                    await self._walk_form_rows(child, item, child_path, extracted)
                else:
                    self._save_form_publication(child, item, extracted)
            except Exception as error:
                failures.append(f"{key}: {error}")
                self.logger.warning("Publication branch deferred: %s", failures[-1])
        if failures:
            raise ValueError("; ".join(failures))

    def _save_form_publication(self, response, item, fields):
        """Finish navigation through existing HTML or binary storage paths."""
        output = item["recipe"]["document"]
        document_type = output.get("type")
        if document_type not in {"html", "pdf"}:
            raise ValueError("Publication document.type must be html or pdf")
        if document_type == "pdf" and not response.body.startswith(b"%PDF-"):
            raise ValueError("Publication action did not return the configured PDF")
        digest = hashlib.sha256(response.body).hexdigest()
        if document_type == "html":
            if not isinstance(response, HtmlResponse) or response.body.startswith(b"%PDF-"):
                raise ValueError("Publication action did not return the configured HTML")
            # Detail-page rules augment listing metadata; no parallel extractor.
            try:
                fields = {**fields, **self._extract_record(response)}
            except Exception:
                # Preserve the received original even if identity fields cannot
                # yet be extracted; successful HTML uses parse_detail's storage.
                self.storage.save_detail_html(digest, response.text)
                raise
        fields = {**fields, **item["recipe"].get("metadata", {}), "FileHash": digest}
        if document_type == "pdf":
            fields.setdefault("PDFURL", response.url)
            fields.setdefault("SourceURL", item["recipe"]["start_url"])
        else:
            fields["SourceURL"] = response.url
        if output.get("identity_fields"):
            fields["SourceRecordId"] = "|".join(str(fields[name]) for name in output["identity_fields"])
        row_evidence = dict(fields)
        record_key = MediaIdentityService.generate_record_key(self.source_config, fields)
        if record_key in self.seen_record_keys:
            previous_record = self.form_publications.get(record_key)
            if previous_record and output.get("conflict_fields"):
                previous_fields = previous_record["extracted"]
                if any(previous_fields.get(name) != fields.get(name)
                       for name in output["conflict_fields"]):
                    previous_fields["SourceMetadataConflict"] = True
                    if row_evidence not in previous_fields["SourceRows"]:
                        previous_fields["SourceRows"].append(row_evidence)
                    if self.persist_pending_work:
                        mediaTodoService.queue_items(self.source_config["dataset_name"], [{"kind": "process", **previous_record}])
                    self.logger.warning("Conflicting source rows retained for publication %s", record_key)
            return
        known = False
        if self.discovery_service:
            known, _ = self.discovery_service.check_record_key(record_key)
            self.discovery_service.record_detail_selected()
        pending = (mediaTodoService.load_items(self.source_config["dataset_name"])
                   if self.persist_pending_work else [])
        previous = next((entry.get("extracted") or {} for entry in pending
                         if entry.get("record_key") == record_key), {})
        fields = {**previous, **fields, "DiscoveryMethods": sorted(set(previous.get("DiscoveryMethods", [])) |
                  {fields.get("DiscoveryMethod", "form_search")}),
                  "RetrievedAt": datetime.now().astimezone().isoformat()}
        if output.get("conflict_fields"):
            # Start fresh on each discovery; stale row conflicts must not survive
            # a subsequent corrected listing through the retry metadata merge.
            fields.pop("SourceRows", None)
            fields.pop("SourceMetadataConflict", None)
            fields.pop("SharedPDFReference", None)
            fields["SourceRows"] = [row_evidence]
        if document_type == "html":
            record = next(self.parse_detail(
                response, record_key, known, fields.get("SourceRecordId"),
                extracted_record=fields))
            record.update(file_url=response.url, discovery_context=item)
        else:
            path = self.storage.save_detail_file(digest, response.body, "pdf")
            record = {"record_key": record_key, "is_known": known, "detail_file_path": path,
                      "file_url": response.url,
                      "extracted": fields, "discovery_context": item}
            self.records.append(record)
        self.seen_record_keys.add(record_key)
        fields = record["extracted"]
        if output.get("conflict_fields"):
            references = self.form_document_references.setdefault(response.url, [])
            if references:
                fields["SharedPDFReference"] = True
                for reference in references:
                    reference["extracted"]["SharedPDFReference"] = True
                    if self.persist_pending_work:
                        mediaTodoService.queue_items(self.source_config["dataset_name"], [{"kind": "process", **reference}])
                self.logger.warning("Different publication identities share document %s", response.url)
            references.append(record)
        if self.persist_pending_work:
            mediaTodoService.queue_items(self.source_config["dataset_name"], [{"kind": "process", **record}])
        self.form_publications[record_key] = record

    def _start_listing_group(self, index):
        """Start the next independent listing, or complete the source."""
        groups = self.discovery_config["groups"]
        service = self.discovery_service
        if getattr(service, "stop_reason", None) == "KNOWN_THRESHOLD_REACHED":
            service.stop_reason = None
        if index == len(groups):
            if not self.group_stopped_early:
                service.mark_source_end()
            elif not getattr(service, "discovery_failure_count", 0):
                service.stop_reason = "KNOWN_THRESHOLD_REACHED"
            return

        group = groups[index]
        service.consecutive_known_records = 0
        service.stop_after_known = (
            self.group_known_stop
            and "detail_urls" not in group
            and "link_selector" not in group
        )
        if "detail_urls" in group:
            for url in group["detail_urls"]:
                request, stop = self._discover_detail(url)
                if request is not None:
                    yield request
                if stop:
                    return
            yield from self._start_listing_group(index + 1)
        elif "link_selector" in group:
            self.group_seen_urls = set()
            self.group_seen_pages = set()
            self.group_seen_page_urls = set()
            yield scrapy.Request(
                group["url"], callback=self.parse_html_listing_group,
                errback=self.html_listing_group_failed,
                cb_kwargs={"index": index},
            )
        else:
            self.group_seen_urls = set()
            self.group_seen_pages = set()
            self.group_last_date = None
            yield self._listing_group_page(index, 0)

    def _html_listing_group_request(self, index):
        """Start one HTML group with state isolated from every other group."""
        group = self.discovery_config["groups"][index]
        self.html_group_states[index] = {
            "seen_urls": set(),
            "seen_pages": set(),
            "seen_page_urls": set(),
        }
        return scrapy.Request(
            group["url"], callback=self.parse_html_listing_group,
            errback=self.html_listing_group_failed,
            cb_kwargs={"index": index},
        )

    def _finish_html_listing_group(self, index):
        """Finish one group and close the source only after every group ends."""
        if not getattr(self, "parallel_html_listing_groups", False):
            yield from self._start_listing_group(index + 1)
            return
        self.completed_html_groups.add(index)
        if len(self.completed_html_groups) == len(self.discovery_config["groups"]):
            self.discovery_service.mark_source_end()

    def _listing_group_page(self, index, offset):
        config = self.discovery_config
        group = config["groups"][index]
        params = build_query(
            config["pagination"], {**config.get("params", {}), **group.get("params", {})},
            page=0, offset=offset,
        )
        return scrapy.Request(
            f'{group.get("url") or config["start_url"]}?{urlencode(params)}',
            callback=self.parse_listing_group, errback=self.listing_group_failed,
            cb_kwargs={"index": index, "offset": offset},
        )

    def _listing_group_url(self, href):
        if not isinstance(href, str) or not href.strip():
            raise ValueError("Article URL is missing")
        base = self.discovery_config["article_base_url"]
        parsed = urlsplit(urljoin(base, href))
        origin = urlsplit(base)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            raise ValueError(f"Unexpected article host: {parsed.netloc}")
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        if not self._extract_source_record_id(url):
            raise ValueError(f"Article URL does not match the configured identity: {url}")
        return url

    def parse_listing_group(self, response, index, offset):
        service = self.discovery_service
        config = self.discovery_config
        try:
            payload = response.json()
            items = read_path(payload, config["items_path"])
            if not isinstance(items, list) or (not items and offset == 0):
                raise ValueError("Missing articles or unexpectedly empty first page")
            hrefs = [read_path(item, config["url_path"]) for item in items]
            # Also detect repeats on a wholly invalid page, where no usable
            # URL would be added to group_seen_urls.
            signature = hashlib.sha256(json.dumps(
                [href if isinstance(href, str) and href.strip() else item
                 for href, item in zip(hrefs, items)], sort_keys=True,
            ).encode()).digest()
            if signature in self.group_seen_pages:
                raise ValueError("Pagination repeated a previous listing page")
        except (TypeError, ValueError) as exc:
            self.logger.error("Invalid listing page %s: %s", response.url, exc)
            service.mark_discovery_failure("INVALID_LISTING_GROUP_PAGE")
            yield from self._start_listing_group(index + 1)
            return

        if not items:
            yield from self._start_listing_group(index + 1)
            return

        self.group_seen_pages.add(signature)
        urls = {}
        invalid_item = False
        ordered = True
        date_field = config.get("sort_date_path")
        for position, (item, href) in enumerate(zip(items, hrefs), start=offset + 1):
            try:
                url = self._listing_group_url(href)
            except ValueError as exc:
                self.logger.warning("Invalid listing item %s in %s: %s", position, response.url, exc)
                service.mark_discovery_failure("INVALID_LISTING_GROUP_ITEM")
                invalid_item = True
                continue
            if url in self.group_seen_urls or url in urls:
                continue
            urls[url] = None
            if date_field:
                try:
                    published = datetime.fromisoformat(read_path(item, date_field))
                    if published.tzinfo is None:
                        raise ValueError("Article date lacks a timezone")
                except (TypeError, ValueError):
                    ordered = False
                    continue
                if self.group_last_date is not None and published > self.group_last_date:
                    ordered = False
                self.group_last_date = published

        if not urls and not invalid_item:
            self.logger.error("Pagination returned no new article URLs: %s", response.url)
            service.mark_discovery_failure("INVALID_LISTING_GROUP_PAGE")
            yield from self._start_listing_group(index + 1)
            return
        if invalid_item or not ordered:
            service.stop_after_known = False
            service.consecutive_known_records = 0
        if not ordered:
            self.logger.warning("Listing order could not be verified, scanning the rest: %s", response.url)

        self.group_seen_urls.update(urls)
        for url in urls:
            request, stop = self._discover_detail(url)
            if request is not None:
                yield request
            if stop:
                if getattr(service, "stop_reason", None) != "KNOWN_THRESHOLD_REACHED":
                    # The DB-free harness uses the same stop signal for its sample cap.
                    return
                self.group_stopped_early = True
                yield from self._start_listing_group(index + 1)
                return

        yield self._listing_group_page(index, offset + len(items))

    def listing_group_failed(self, failure):
        self.logger.error("Listing group fetch failed: %s", failure)
        self.discovery_service.mark_discovery_failure("LISTING_GROUP_FETCH_FAILED")
        yield from self._start_listing_group(failure.request.cb_kwargs["index"] + 1)

    def parse_html_listing_group(self, response, index):
        """Discover one HTML listing group, following its next link when configured."""
        group = self.discovery_config["groups"][index]
        pagination = group.get("pagination")
        if getattr(self, "parallel_html_listing_groups", False):
            state = self.html_group_states[index]
            seen_urls = state["seen_urls"]
            seen_pages = state["seen_pages"]
            seen_page_urls = state["seen_page_urls"]
        else:
            seen_urls = self.group_seen_urls
            seen_pages = self.group_seen_pages
            seen_page_urls = self.group_seen_page_urls
        if pagination and pagination.get("type", "next_link") != "next_link":
            raise ValueError("HTML listing groups support next-link pagination only")
        if response.url in seen_page_urls:
            self.discovery_service.mark_discovery_failure("REPEATED_HTML_LISTING_GROUP_PAGE")
            yield from self._finish_html_listing_group(index)
            return
        seen_page_urls.add(response.url)
        hrefs = response.css(f'{group["link_selector"]}::attr(href)').getall()
        if not hrefs:
            if pagination and len(seen_page_urls) > 1:
                self.logger.info("HTML listing group reached its end: %s", response.url)
                yield from self._finish_html_listing_group(index)
                return
            self.logger.error("HTML listing has no detail links: %s", response.url)
            self.discovery_service.mark_discovery_failure("EMPTY_HTML_LISTING_GROUP")
            yield from self._finish_html_listing_group(index)
            return

        signature = hashlib.sha256(json.dumps(hrefs).encode()).digest()
        if signature in seen_pages:
            self.discovery_service.mark_discovery_failure("REPEATED_HTML_LISTING_GROUP_PAGE")
            yield from self._finish_html_listing_group(index)
            return
        seen_pages.add(signature)

        urls = []
        for href in hrefs:
            try:
                url = self._listing_group_url(response.urljoin(href))
            except ValueError as exc:
                self.logger.warning("Invalid HTML listing link in %s: %s", response.url, exc)
                self.discovery_service.mark_discovery_failure("INVALID_HTML_LISTING_GROUP_ITEM")
                continue
            if url not in urls:
                urls.append(url)

        if not urls:
            self.discovery_service.mark_discovery_failure("EMPTY_HTML_LISTING_GROUP")
            yield from self._finish_html_listing_group(index)
            return

        new_urls = [url for url in urls if url not in seen_urls]
        if pagination and not new_urls:
            self.discovery_service.mark_discovery_failure("HTML_LISTING_GROUP_NO_NEW_URLS")
            yield from self._finish_html_listing_group(index)
            return
        seen_urls.update(new_urls)

        if group.get("include_page"):
            request, stop = self._discover_detail(response.url)
            if request is not None:
                yield from self.parse_detail(response, **request.cb_kwargs)
            if stop:
                return

        for url in new_urls:
            request, stop = self._discover_detail(url)
            if request is not None:
                yield request
            if stop:
                return

        if pagination:
            links = response.css(pagination["selector"])
            if len(links) > 1:
                self.discovery_service.mark_discovery_failure("INVALID_HTML_LISTING_GROUP_NEXT")
                yield from self._finish_html_listing_group(index)
                return
            if links:
                try:
                    next_url = resolve_next_page_url(
                        response.url, links[0].attrib.get("href", ""),
                        visited_urls=seen_page_urls,
                    )
                except ValueError as exc:
                    self.logger.error("Invalid HTML listing next link in %s: %s", response.url, exc)
                    self.discovery_service.mark_discovery_failure("INVALID_HTML_LISTING_GROUP_NEXT")
                    yield from self._finish_html_listing_group(index)
                    return
                yield scrapy.Request(
                    next_url, callback=self.parse_html_listing_group,
                    errback=self.html_listing_group_failed,
                    cb_kwargs={"index": index},
                    priority=10,
                )
                return

        yield from self._finish_html_listing_group(index)

    def html_listing_group_failed(self, failure):
        self.logger.error("HTML listing group fetch failed: %s", failure)
        self.discovery_service.mark_discovery_failure("HTML_LISTING_GROUP_FETCH_FAILED")
        yield from self._finish_html_listing_group(failure.request.cb_kwargs["index"])

    def _rss_page_url(self, feed_url, offset):
        parsed = urlsplit(feed_url)
        params = build_query(
            self.rss_pagination, dict(parse_qsl(parsed.query)), page=0, offset=offset,
        )
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), parsed.fragment))

    def parse_rss(self, response, feed_url=None, offset=0):
        paginated = bool(getattr(self, "rss_pagination", None))
        try:
            channel = ElementTree.fromstring(response.body).find("channel")
            if channel is None:
                raise ValueError("RSS channel not found")

            items = channel.findall("item")
            if not items:
                if not paginated or offset == 0:
                    raise ValueError("RSS feed contains no items")
                self.finish_rss_feed()
                return

            if paginated:
                signature = tuple(item.findtext("link", "").strip() for item in items)
                if signature in self.rss_seen_pages:
                    raise ValueError("RSS pagination repeated a previous page")
                self.rss_seen_pages.add(signature)

                dates = []
                for item in items:
                    try:
                        published = parsedate_to_datetime(item.findtext("pubDate", ""))
                        if published.tzinfo is None:
                            raise ValueError("RSS date has no timezone")
                    except (TypeError, ValueError):
                        published = None
                    dates.append(published)
                ordered = all(
                    published is not None and (
                        previous is None or published <= previous
                    )
                    for published, previous in zip(dates, [self.rss_last_date, *dates[:-1]])
                )
                if not ordered:
                    self.logger.warning("RSS date order could not be verified: %s", response.url)
                    self.discovery_service.stop_after_known = False
                self.rss_last_date = dates[-1]

            article_host = urlparse(self.task.url).hostname
            for item, published in zip(items, dates if paginated else [None] * len(items)):
                url = item.findtext("link", "").strip()
                parsed = urlparse(url)
                if (
                    parsed.scheme not in {"https", "http"}
                    or parsed.hostname != article_host
                ):
                    self.discovery_service.mark_discovery_failure(
                        "INVALID_RSS_ARTICLE_URL", prevent_known_stop=paginated,
                    )
                    continue

                article_url_pattern = self.discovery_config.get("article_url_pattern")
                if article_url_pattern and not re.search(article_url_pattern, url):
                    continue
                if not self._extract_source_record_id(url):
                    self.discovery_service.mark_discovery_failure(
                        "INVALID_RSS_ARTICLE_URL", prevent_known_stop=paginated,
                    )
                    continue

                request, should_stop = self._discover_detail(url)
                if request is not None:
                    if self.discovery_config.get("use_item_pub_date"):
                        if published is None:
                            self.discovery_service.mark_discovery_failure(
                                "INVALID_RSS_DATE", prevent_known_stop=paginated,
                            )
                        else:
                            request.cb_kwargs["identity_fields"] = {
                                **request.cb_kwargs.get("identity_fields", {}),
                                "date.originalValue": published.date().isoformat(),
                            }
                    yield request
                if paginated and should_stop:
                    self.finish_rss_feed(reached_end=False)
                    return

            if paginated:
                yield scrapy.Request(
                    self._rss_page_url(feed_url, offset + len(items)),
                    callback=self.parse_rss,
                    errback=self.rss_failed,
                    cb_kwargs={"feed_url": feed_url, "offset": offset + len(items)},
                )
                return

        except (ElementTree.ParseError, ValueError) as exc:
            self.logger.warning("Invalid RSS feed %s: %s", response.url, exc)
            self.discovery_service.mark_discovery_failure("INVALID_RSS_FEED")
        self.finish_rss_feed()

    def rss_failed(self, failure):
        self.logger.warning("RSS download failed: %s", failure)
        self.discovery_service.mark_discovery_failure("RSS_FETCH_FAILED")
        self.finish_rss_feed()

    def finish_rss_feed(self, *, reached_end=True):
        self.pending_rss_feeds -= 1
        if self.pending_rss_feeds == 0 and reached_end:
            self.discovery_service.mark_source_end()

    def parse_month_archive(self, response):
        """Visit month indexes serially so URL checks follow decision dates."""
        months = {}
        for href in response.css(self.discovery_config["month_link_selector"] + "::attr(href)").getall():
            url = response.urljoin(href)
            match = re.search(r"/docmonth/([A-Za-z]{3})/(\d{4})/1/?$", url)
            if not match or urlparse(url).netloc != urlparse(response.url).netloc:
                self.discovery_service.mark_discovery_failure("INVALID_MONTH_URL")
                return
            try:
                month = datetime.strptime(match.group(1), "%b").month
            except ValueError:
                self.discovery_service.mark_discovery_failure("INVALID_MONTH_URL")
                return
            months[(int(match.group(2)), month)] = url
        if not months:
            self.discovery_service.mark_discovery_failure("EMPTY_MONTH_ARCHIVE")
            return
        newest = getattr(self.discovery_service, "stop_after_known", False)
        urls = [months[key] for key in sorted(months, reverse=newest)]
        yield scrapy.Request(urls[0], callback=self.parse_month_index,
                             errback=self.month_index_failed,
                             cb_kwargs={"urls": urls, "index": 0, "newest": newest})

    def _month_entries(self, response):
        nodes = response.css(self.discovery_config["article"]["link_selector"])
        entries = []
        try:
            month = re.search(r"/docmonth/([A-Za-z]{3})/(\d{4})/1/?$", response.url)
            headings = " ".join(response.css("#left h3::text").getall())
            if not month or not re.search(rf"\b{month.group(1)}\s+{month.group(2)}\s*\|\s*Decisions\s*/\s*Signed Resolutions", headings, re.I):
                raise ValueError("Month heading is missing or does not match URL")
            for node in nodes:
                label = " ".join(node.xpath(".//text()").getall())
                match = re.search(r"([A-Za-z]+\s+\d{1,2},\s+\d{4})\s*$", label)
                if not match or not node.attrib.get("href"):
                    raise ValueError("Missing decision date or URL")
                entries.append((datetime.strptime(match.group(1), "%B %d, %Y"), node))
        except ValueError:
            raise
        return entries

    def parse_month_index(self, response, urls, index, newest):
        try:
            entries = self._month_entries(response)
        except ValueError as exc:
            self.logger.error("Invalid month index %s: %s", response.url, exc)
            self.discovery_service.mark_discovery_failure("INVALID_MONTH_INDEX", retry_item={"kind": "month_index", "index_file_url": response.url, "error": str(exc)})
            return
        for _, node in sorted(entries, key=lambda entry: entry[0], reverse=newest):
            request, stop = self._discover_detail(response.urljoin(node.attrib["href"]), node)
            if request is not None:
                yield request
            if stop:
                return
        if index + 1 == len(urls):
            self.discovery_service.mark_source_end()
        else:
            yield scrapy.Request(urls[index + 1], callback=self.parse_month_index,
                                 errback=self.month_index_failed,
                                 cb_kwargs={"urls": urls, "index": index + 1, "newest": newest})

    def month_index_failed(self, failure):
        url = failure.request.url
        self.logger.error("Month index request failed: %s", failure)
        self.discovery_service.mark_discovery_failure("MONTH_INDEX_FETCH_FAILED", retry_item={"kind": "month_index", "index_file_url": url, "error": failure.getErrorMessage()})

    def retry_month_index(self, response, retry_item):
        """Rediscover every decision in a failed month, then clear the month item."""
        try:
            expected = urlparse(retry_item["index_file_url"])
            actual = urlparse(response.url)
            if (expected.scheme, expected.netloc) != (actual.scheme, actual.netloc):
                raise ValueError("Month response redirected to another host")
            entries = self._month_entries(response)
            requests = []
            for _, node in sorted(entries, key=lambda entry: entry[0]):
                url = response.urljoin(node.attrib["href"])
                parsed = urlparse(url)
                if (parsed.scheme, parsed.netloc) != (actual.scheme, actual.netloc):
                    raise ValueError("Decision URL is not on the source host")
                source_id = self._extract_source_record_id(url)
                key = MediaIdentityService.generate_record_key(source_config=self.source_config, record={"SourceURL": url, "SourceRecordId": source_id})
                requests.append(scrapy.Request(url, callback=self.parse_detail, errback=self.detail_failed, cb_kwargs={"record_key": key, "is_known": False, "source_record_id": source_id, "identity_fields": {}}, dont_filter=True))
        except (KeyError, TypeError, ValueError) as exc:
            yield self._failed_json_entry_retry(retry_item, exc)
            return
        marker = {"record_key": retry_item["record_key"], "discovery_retry_complete": True}
        self.records.append(marker)
        yield marker
        yield from requests

    def json_discovery_failed(self, failure):
        """A failed manifest or shard must never count as a complete scan."""
        self.logger.error("JSON discovery request failed: %s", failure)
        self.discovery_service.mark_discovery_failure("JSON_DISCOVERY_FETCH_FAILED")

    def parse_json_manifest(self, response):
        """Read an ordered manifest and request its first JSON item file."""
        config = self.discovery_config.get("json_manifest", {})
        try:
            manifest = response.json()
            shards = manifest[config.get("shards_field", "shards")]
            if not isinstance(shards, list) or not shards:
                raise ValueError("manifest has no shards")
            urls = []
            counts = []
            for shard in shards:
                path = shard[config.get("path_field", "path")]
                count = shard[config.get("count_field", "count")]
                if not isinstance(path, str) or not path:
                    raise ValueError("manifest shard path is missing")
                if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                    raise ValueError("manifest shard count is invalid")
                url = response.urljoin(path)
                if (urlparse(url).scheme, urlparse(url).netloc) != (urlparse(response.url).scheme, urlparse(response.url).netloc):
                    raise ValueError("manifest shard must be on the same host")
                urls.append(url)
                counts.append(count)
        except (KeyError, TypeError, ValueError) as exc:
            self.logger.error("Invalid JSON manifest: %s", exc)
            self.discovery_service.mark_discovery_failure("INVALID_JSON_MANIFEST")
            return

        yield scrapy.Request(
            urls[0], callback=self.parse_json_shard,
            errback=self.json_discovery_failed,
            cb_kwargs={"urls": urls, "counts": counts, "index": 0,
                       "previous_date": None},
        )

    def parse_json_shard(self, response, urls, counts, index, previous_date):
        """Discover URLs from ordered JSON items."""
        config = self.discovery_config.get("json_manifest", {})
        try:
            items = response.json()[config.get("items_field", "items")]
            if not isinstance(items, list) or len(items) != counts[index]:
                raise ValueError("shard item count does not match manifest")
            checked = []
            last_date = previous_date
            for position, item in enumerate(items, start=1):
                try:
                    raw_date = item[config.get("date_field", "date")]
                    item_date = date.fromisoformat(raw_date).isoformat()
                    href = item[config.get("url_field", "url")]
                    if not isinstance(href, str) or not href:
                        raise ValueError("item URL is missing")
                    url = response.urljoin(href)
                    if (urlparse(url).scheme, urlparse(url).netloc) != (urlparse(response.url).scheme, urlparse(response.url).netloc):
                        raise ValueError("item URL must be on the same host")
                    if last_date and item_date > last_date:
                        raise ValueError("items are not in descending date order")
                    checked.append((item_date, url))
                    last_date = item_date
                except (KeyError, TypeError, ValueError) as exc:
                    self.logger.error(
                        "Invalid JSON item in %s at position %s: %s; item=%r",
                        response.url, position, exc, item,
                    )
                    # An invalid item breaks the assumption behind stopping at
                    # known records, so continue through the remaining files.
                    self.discovery_service.mark_discovery_failure(
                        "INVALID_JSON_ITEM", prevent_known_stop=True,
                        retry_item={
                            "kind": "json_manifest_entry",
                            "index_file_url": response.url,
                            "position": position,
                            "entry": item,
                            "entry_url": item.get(config.get("url_field", "url")) if isinstance(item, dict) else None,
                            "previous_entry": items[position - 2] if position > 1 else None,
                            "next_entry": items[position] if position < len(items) else None,
                            "error": str(exc),
                        },
                    )
                    last_date = None
        except (KeyError, TypeError, ValueError) as exc:
            self.logger.error("Invalid JSON shard %s: %s", response.url, exc)
            self.discovery_service.mark_discovery_failure("INVALID_JSON_SHARD")
            return

        for _, detail_url in checked:
            request, should_stop = self._discover_detail(detail_url)
            if request is not None:
                yield request
            if should_stop:
                return

        if index + 1 < len(urls):
            yield scrapy.Request(
                urls[index + 1], callback=self.parse_json_shard,
                errback=self.json_discovery_failed,
                cb_kwargs={"urls": urls, "counts": counts, "index": index + 1,
                           "previous_date": last_date},
            )
        else:
            self.discovery_service.mark_source_end()

    def _failed_json_entry_retry(self, retry_item, error):
        """Return an unresolved discovery item through the existing to-do pipeline."""
        record = {
            "record_key": retry_item["record_key"], "failed": True,
            "discovery_retry_item": retry_item, "error": str(error),
            "error_stage": "DISCOVERY", "error_type": "DiscoveryRetryError",
        }
        self.records.append(record)
        return record

    def retry_json_entry_failed(self, failure):
        retry_item = failure.request.cb_kwargs["retry_item"]
        yield self._failed_json_entry_retry(retry_item, failure.getErrorMessage())

    def retry_json_entry(self, response, retry_item):
        """Retry an entry identified by a unique article URL, never by position."""
        config = self.discovery_config.get("json_manifest", {})
        url_field = config.get("url_field", "url")
        date_field = config.get("date_field", "date")
        original_url = retry_item.get("entry_url")
        try:
            expected = urlparse(retry_item["index_file_url"])
            actual = urlparse(response.url)
            if (expected.scheme, expected.netloc) != (actual.scheme, actual.netloc):
                raise ValueError("Index response redirected to another host")
            items = response.json()[config.get("items_field", "items")]
            if not isinstance(items, list) or not isinstance(original_url, str) or not original_url:
                raise ValueError("Cannot safely identify the original entry; manual review required")
            matches = [(i, item) for i, item in enumerate(items)
                       if isinstance(item, dict) and item.get(url_field) == original_url]
            if len(matches) != 1:
                raise ValueError("Original entry is missing or ambiguous; manual review required")
            index, item = matches[0]
            item_date = date.fromisoformat(item[date_field])
            for neighbor_index in (index - 1, index + 1):
                if 0 <= neighbor_index < len(items):
                    neighbor_date = date.fromisoformat(items[neighbor_index][date_field])
                    if ((neighbor_index < index and neighbor_date < item_date) or
                            (neighbor_index > index and neighbor_date > item_date)):
                        raise ValueError("Entry is not in descending date order")
            detail_url = response.urljoin(original_url)
            if (urlparse(detail_url).scheme, urlparse(detail_url).netloc) != (urlparse(response.url).scheme, urlparse(response.url).netloc):
                raise ValueError("item URL must be on the same host")
            source_record_id = self._extract_source_record_id(detail_url)
            record_key = MediaIdentityService.generate_record_key(
                source_config=self.source_config,
                record={"SourceURL": detail_url, "SourceRecordId": source_record_id},
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            yield self._failed_json_entry_retry(retry_item, exc)
            return
        yield scrapy.Request(
            detail_url, callback=self.parse_detail, errback=self.detail_failed,
            cb_kwargs={"record_key": record_key, "is_known": False,
                       "source_record_id": source_record_id, "identity_fields": {},
                       "discovery_retry_key": retry_item["record_key"]},
            dont_filter=True,
        )

    def _discover_detail(self, detail_url, link_node=None):
        """Apply shared identity and selection rules to one discovered detail URL."""
        try:
            source_record_id = self._extract_source_record_id(detail_url)
            identity_fields = (
                self._extract_identity_fields(link_node) if link_node is not None else {}
            )
            record_key = self.discovery_service.build_record_key({
                "SourceURL": detail_url,
                "SourceRecordId": source_record_id,
                **identity_fields,
            })
        except Exception as exc:
            # Preserve the listing parser's existing error handling: unexpected
            # identity errors remain visible instead of being swallowed.
            if link_node is not None and not isinstance(exc, ValueError):
                raise
            self.discovery_service.record_identity_failure()
            self.logger.warning("Could not build media identity for URL %s: %s", detail_url, exc)
            return None, False

        # Ignore duplicate links before they affect
        # KNOWN counters.
        if record_key in self.seen_record_keys:
            self.logger.debug("Duplicate media link ignored | key=%s", record_key)
            return None, False
        self.seen_record_keys.add(record_key)

        try:
            is_known, should_stop = self.discovery_service.check_record_key(record_key)
        except Exception as exc:
            self.discovery_service.record_identity_failure()
            self.logger.warning("Could not check media record key=%s: %s", record_key, exc)
            return None, False

        self.logger.info("Media discovered | key=%s | known=%s", record_key, is_known)
        # A full scan fetches known records again so
        # content_hash can detect an updated article.
        # stop_after_known sources skip known detail
        # downloads and use them only as the safe
        # incremental stopping signal.
        if is_known and (
            self.discovery_policy != "full_scan" or self.discovery_config.get("skip_known_details")
        ):
            self.logger.info("Known Media detail download skipped | key=%s", record_key)
            return None, should_stop

        self.discovery_service.record_detail_selected()
        return scrapy.Request(
            url=detail_url,
            callback=self.parse_detail,
            errback=self.detail_failed,
            cb_kwargs={
                "record_key": record_key,
                "is_known": is_known,
                "source_record_id": source_record_id,
                "identity_fields": identity_fields,
            },
        ), should_stop

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

        end_status = self._pagination_end_status()

        if end_status and response.status == end_status:
            self.logger.info(
                "Listing page %s returned %s; source end reached.",
                page_number,
                response.status,
            )
            self.discovery_service.mark_source_end()
            return

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
            start_page = int(
                self.discovery_config.get(
                    "start_page",
                    1,
                )
            )

            if (
                page_number == start_page
                and not self.discovery_config.get(
                    "allow_empty_listing",
                    False,
                )
            ):
                self.logger.error(
                    "No media links found on the first "
                    "listing page; treating the run as "
                    "incomplete. page=%s",
                    page_number,
                )
                self.discovery_service.mark_discovery_failure(
                    "EMPTY_FIRST_LISTING_PAGE"
                )

            else:
                self.logger.info(
                    "No media links found on page %s; "
                    "source end reached.",
                    page_number,
                )
                self.discovery_service.mark_source_end()
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

            request, should_stop = self._discover_detail(detail_url, link_node)
            if request is not None:
                yield request

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
            self.discovery_service.mark_source_end()
            return

        end_status = self._pagination_end_status()

        yield scrapy.Request(
            url=next_url,
            callback=self.parse_listing,
            cb_kwargs={
                "page_number": page_number + 1,
            },
            meta=(
                {"handle_httpstatus_list": [end_status]}
                if end_status
                else {}
            ),
        )

    def parse_list_rows(
        self,
        response,
    ):
        """Build one record per listing row for list_only sources (no detail pages)."""

        row_selector = self.discovery_config.get(
            "article",
            {},
        ).get(
            "row_selector"
        )

        if not row_selector:
            raise ValueError(
                "discovery.article.row_selector "
                "is required for list_only."
            )

        rows = response.css(
            row_selector
        )

        if not rows:
            self.logger.error(
                "No media rows found on the listing; "
                "treating the run as incomplete."
            )
            self.discovery_service.mark_discovery_failure(
                "EMPTY_FIRST_LISTING_PAGE"
            )
            return

        for row in rows:

            try:
                identity_fields = (
                    self._extract_identity_fields(
                        row
                    )
                )

                record_key = (
                    self.discovery_service
                    .build_record_key(
                        {
                            "SourceURL": response.url,
                            **identity_fields,
                        }
                    )
                )

            except ValueError as exc:
                self.discovery_service.record_identity_failure()
                self.logger.warning(
                    "Could not build media identity "
                    "for listing row: %s",
                    exc,
                )
                continue

            if record_key in self.seen_record_keys:
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
                self.discovery_service.record_identity_failure()
                self.logger.warning(
                    "Could not check media record "
                    "key=%s: %s",
                    record_key,
                    exc,
                )
                continue

            self.discovery_service.record_detail_selected()

            row_response = HtmlResponse(
                url=response.url,
                body=row.get(),
                encoding="utf-8",
            )

            yield from self.parse_detail(
                response=row_response,
                record_key=record_key,
                is_known=is_known,
                source_record_id=None,
                identity_fields=identity_fields,
            )

            if should_stop:
                return

        self.discovery_service.mark_source_end()

    def parse_detail(
        self,
        response,
        record_key: str,
        is_known: bool,
        source_record_id: str | None,
        identity_fields: dict[str, Any] | None = None,
        discovery_retry_key: str | None = None,
        extracted_record: dict[str, Any] | None = None,
    ):
        """
        Save original HTML first, then extract fields.

        Saving HTML before extraction ensures the original
        source remains available even if extraction fails.

        Configured navigation may supply already extracted fields, preserving
        parent metadata without extracting the final page a second time.
        """

        file_name_id = self._build_file_name_id(
            record_key=record_key,
        )

        # Save the original HTML before any parsing/extraction.
        detail_file_path = (
            self.storage.save_detail_html(
                record_id=file_name_id,
                content=response.text,
            )
        )

        try:
            extracted_record = (self._extract_record(response) if extracted_record is None
                                else dict(extracted_record))

        except Exception as error:
            self.logger.exception(
                "Media detail extraction failed. url=%s",
                response.url,
            )

            failed_record = self.failed_detail_record(
                detail_url=response.url,
                details={
                    "record_key": record_key,
                    "is_known": is_known,
                    "source_record_id": source_record_id,
                    "identity_fields": identity_fields,
                    "discovery_retry_key": discovery_retry_key,
                },
                error=str(error),
                error_type=type(error).__name__,
                error_stage="DETAIL_EXTRACTION",
            )

            self.records.append(
                failed_record
            )

            yield failed_record
            return

        # Ensure discovery and detail extraction use
        # the same canonical URL.
        extracted_record["SourceURL"] = (
            response.url
        )

        if source_record_id:
            extracted_record[
                "SourceRecordId"
            ] = source_record_id

        if identity_fields:
            extracted_record.update(
                identity_fields
            )

        result = {
            "record_key": record_key,
            "is_known": is_known,
            "detail_file_path": detail_file_path,
            "extracted": extracted_record,
            "discovery_retry_key": discovery_retry_key,
        }

        self.records.append(
            result
        )

        yield result

    def detail_failed(
        self,
        failure,
    ):
        """Keep a failed detail request as a record so it can be retried."""

        request = failure.request

        self.logger.error(
            "Media detail fetch failed. url=%s error=%s",
            request.url,
            failure.getErrorMessage(),
        )

        failed_record = self.failed_detail_record(
            detail_url=request.url,
            details=request.cb_kwargs,
            error=failure.getErrorMessage(),
            error_type=type(failure.value).__name__,
        )

        self.records.append(
            failed_record
        )

        yield failed_record

    @staticmethod
    def failed_detail_record(
        detail_url: str | None,
        details: dict[str, Any],
        error: str,
        error_type: str,
        error_stage: str = "DETAIL_FETCH",
    ) -> dict[str, Any]:
        return {
            "record_key": details.get("record_key"),
            "is_known": details.get("is_known"),
            "detail_url": detail_url,
            "source_record_id": details.get("source_record_id"),
            "identity_fields": details.get("identity_fields") or {},
            "discovery_retry_key": details.get("discovery_retry_key"),
            "detail_file_path": None,
            "extracted": None,
            "failed": True,
            "error": error,
            "error_stage": error_stage,
            "error_type": error_type,
        }

    @staticmethod
    def detail_item_kwargs(
        item: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "record_key": item["record_key"],
            "is_known": False,
            "source_record_id": item.get("source_record_id"),
            "identity_fields": item.get("identity_fields") or {},
        }

    def _extract_record(
        self,
        response,
        *,
        fields: dict[str, Any] | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Extract fields from a full page or a selected row using the same rules.

        Existing detail-page callers use source-level extraction fields. A
        navigation stage supplies its own fields and the parent page's base URL
        for relative links and source: response_url. An empty fields dictionary
        deliberately extracts nothing; it does not select source-level defaults.

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

        extraction_fields = self.extraction_config if fields is None else fields
        if fields is None:
            for variant in self.source_config.get("extraction_by_url", []):
                if re.search(variant["pattern"], response.url):
                    extraction_fields = {
                        **extraction_fields,
                        **{
                            name: {**extraction_fields.get(name, {}), **overrides}
                            for name, overrides in variant["fields"].items()
                        },
                    }
                    break

        for (
            field_name,
            field_config,
        ) in extraction_fields.items():

            record[field_name] = (
                self._extract_field(
                    response=response,
                    field_config=field_config,
                    base_url=base_url,
                )
            )

        return record

    def _extract_field(
        self,
        response,
        field_config: dict[str, Any],
        base_url: str | None = None,
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

        multiple: true         -> return a de-duped list of every match
        prefix: "https://..."  -> prepend to the value (or each list item)

        extraction:
            strategy: regex
            pattern: "..."
        """

        source = field_config.get(
            "source"
        )

        if source == "response_url":
            value = base_url or response.url

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

            selected_nodes = (response.xpath(selector) if field_config.get("selector_type") == "xpath"
                              else response.css(selector))

            if not selected_nodes:
                if field_config.get("required"):
                    raise ValueError(f"Required extraction field missing: {selector}")
                return None

            multiple = bool(
                field_config.get("multiple", False)
            )

            if output == "text":
                join_with = field_config.get("join_with")
                if join_with is not None:
                    texts = [self._clean_text(node.xpath("string(.)").get()) for node in selected_nodes]
                    value = join_with.join(text for text in texts if text) or None
                elif multiple:
                    value = [
                        self._clean_text(
                            " ".join(
                                node.xpath(".//text()").getall()
                            )
                        )
                        for node in selected_nodes
                    ]
                    value = [item for item in value if item]

                else:
                    value = self._clean_text(
                        " ".join(
                            selected_nodes[0].xpath(".//text()").getall()
                        )
                    )

            elif output == "html":

                value = (
                    selected_nodes.getall()
                    if multiple
                    else selected_nodes.get()
                )

            elif output == "attribute":

                attribute = field_config.get(
                    "attribute"
                )

                if not attribute:
                    raise ValueError(
                        "attribute is required "
                        "when output=attribute"
                    )

                nodes = (
                    selected_nodes
                    if multiple
                    else selected_nodes[:1]
                )

                collected = []

                for node in nodes:

                    raw = node.attrib.get(attribute)

                    if not raw:
                        continue

                    if attribute in {"href", "src"}:
                        raw = urljoin(base_url or response.url, raw)

                    if raw not in collected:
                        collected.append(raw)

                if multiple:
                    value = collected
                else:
                    value = collected[0] if collected else None

            else:
                raise ValueError(
                    f"Unsupported extraction output: "
                    f"{output}"
                )

        extraction = field_config.get(
            "extraction"
        )

        if extraction and not isinstance(value, list):
            value = self._apply_extraction(
                value=value,
                extraction=extraction,
            )

        if "values" in field_config and value is not None:
            value = field_config["values"].get(value, value)
        if field_config.get("required") and not value:
            raise ValueError(f"Required extraction field missing: {field_config.get('selector')}")

        prefix = field_config.get("prefix")

        if prefix and value:
            if isinstance(value, list):
                value = [f"{prefix}{item}" for item in value]
            else:
                value = f"{prefix}{value}"

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

    def _extract_identity_fields(
        self,
        link_node,
    ) -> dict[str, str]:
        """Extract configured candidate fields from one listing link."""

        article_config = self.discovery_config.get(
            "article",
            {},
        )

        identity_fields_config = article_config.get(
            "identity_fields",
            {},
        )

        extracted_fields: dict[str, str] = {}

        for field_name, field_config in (
            identity_fields_config.items()
        ):
            source = str(
                field_config.get(
                    "source",
                    "xpath",
                )
            ).strip().lower()

            raw_value = None

            if source == "link_text":
                raw_value = " ".join(
                    link_node.xpath(
                        ".//text()"
                    ).getall()
                )

            elif source == "xpath":
                selectors = field_config.get(
                    "selectors",
                    [],
                )

                if isinstance(selectors, str):
                    selectors = [selectors]

                for selector in selectors:
                    selected = link_node.xpath(
                        selector
                    ).getall()

                    candidate = self._clean_text(
                        " ".join(selected)
                    )

                    if candidate:
                        raw_value = candidate
                        break

            else:
                raise ValueError(
                    "Unsupported identity field "
                    f"source: {source}"
                )

            cleaned_value = self._clean_text(
                raw_value
            )

            if (
                not cleaned_value
                and field_config.get(
                    "required",
                    True,
                )
            ):
                raise ValueError(
                    "Missing listing field required for identity: "
                    f"field: {field_name}"
                )

            extracted_fields[field_name] = (
                cleaned_value or ""
            )

        return extracted_fields

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

    def _pagination_end_status(
        self,
    ) -> int | None:
        """Return the HTTP status that marks the end of pages, if the source declares one."""

        end_status = self.discovery_config.get(
            "pagination",
            {},
        ).get("end_status")

        return int(end_status) if end_status else None

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
    ) -> str:
        """Build one safe and stable filename from record_key."""
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
