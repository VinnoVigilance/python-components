"""
Unit tests for the API collector's paging logic.

Real collection hits live APIs, so these tests never touch the network: they
drive the pure paging helpers directly and drive ``_iter_pages`` with the
per-page HTTP call (``_get_page``) patched out.

Two behaviours are locked in here:

  1. **Existing paged sources (e.g. FBI-WANTED) are unchanged.** They keep
     injecting a ``page`` parameter and keep looping until an empty page.
  2. **New single-request sources (``type: "none"``)** fetch exactly once and
     stop -- even when the endpoint ignores paging and would otherwise return
     the same non-empty list forever.
"""

from unittest.mock import patch

import pytest

from ingestion.apiCollector import collector
from ingestion.apiCollector.models import ApiCollectorTask
from ingestion.apiCollector.pagination import (
    build_query,
    extract_items,
    should_stop,
)

pytestmark = pytest.mark.unit


# --- build_query -----------------------------------------------------------

class TestBuildQuery:
    def test_fbi_style_paging_injects_page_and_size(self):
        # This is exactly FBI-WANTED's api_config pagination block. The query
        # it builds must not change: static params + page + pageSize.
        pagination = {
            "type": "page",
            "page_param": "page",
            "size_param": "pageSize",
            "page_size": 50,
            "start_page": 1,
        }

        query = build_query(pagination=pagination, params={}, page=3)

        assert query == {"page": 3, "pageSize": 50}

    def test_default_type_still_paginates(self):
        # A config that omits "type" must behave like before this change:
        # the page param is still injected.
        query = build_query(pagination={}, params={"category": "X"}, page=2)

        assert query == {"category": "X", "page": 2}

    def test_single_request_omits_page_param(self):
        # type: "none" -> only the static params, never a page number.
        pagination = {"type": "none"}

        query = build_query(
            pagination=pagination,
            params={"category": "BLACKLISTED_ENTITIES"},
            page=1,
        )

        assert query == {"category": "BLACKLISTED_ENTITIES"}

    def test_offset_paging_multiplies_counter_by_page_size(self):
        # ADB's api_config: the zero-based loop counter becomes a record offset
        # (offset = counter * page_size), and the size param rides along.
        pagination = {
            "type": "offset",
            "offset_param": "offset",
            "size_param": "size",
            "page_size": 100,
            "start_page": 0,
        }

        first = build_query(
            pagination=pagination, params={"sortField": "Name"}, page=0
        )
        third = build_query(pagination=pagination, params={}, page=3)

        assert first == {"sortField": "Name", "offset": 0, "size": 100}
        assert third == {"offset": 300, "size": 100}


# --- extract_items ---------------------------------------------------------

class TestExtractItems:
    def test_dotted_path(self):
        payload = {"items": [1, 2, 3]}
        assert extract_items(payload, "items") == [1, 2, 3]

    def test_empty_path_returns_root_list(self):
        # GPPB returns a bare top-level array (no wrapper object), so the
        # collector reads it with items_path = "".
        payload = [{"a": 1}, {"a": 2}]
        assert extract_items(payload, "") == [{"a": 1}, {"a": 2}]


# --- should_stop -----------------------------------------------------------

class TestShouldStop:
    def test_stops_on_empty(self):
        assert should_stop([]) is True

    def test_continues_on_non_empty(self):
        assert should_stop([{"a": 1}]) is False


# --- _iter_pages -----------------------------------------------------------

def _task(pagination, items_path="items", params=None, param_variants=None):
    return ApiCollectorTask(
        url="https://example.test/api",
        source_name="TEST",
        list_name="TEST",
        pagination=pagination,
        items_path=items_path,
        params=params or {},
        param_variants=param_variants or [],
    )


class TestIterPages:
    def test_paged_source_loops_until_empty(self):
        # BEFORE/AFTER guard for FBI-style sources: page 1 and 2 have records,
        # page 3 is empty -> exactly two pages are yielded, then it stops.
        task = _task(
            {
                "type": "page",
                "page_param": "page",
                "start_page": 1,
            }
        )

        pages = {
            1: {"items": [{"id": 1}]},
            2: {"items": [{"id": 2}]},
            3: {"items": []},
        }

        def fake_get_page(_task, query, _transport=None):
            return pages[query["page"]]

        with patch.object(collector, "_get_page", side_effect=fake_get_page):
            result = list(collector._iter_pages(task, None))

        assert result == [[{"id": 1}], [{"id": 2}]]

    def test_auth_failure_raises_immediately_without_retry(self):
        # A 401/403 will not fix itself on retry and must not look like an
        # empty result. The transport now owns the HTTP call, so it is the
        # transport that raises a clear error on the first response -- without
        # a second request.
        from ingestion.apiCollector import transport as transport_mod

        calls = {"count": 0}

        class _Resp:
            status_code = 401

            def raise_for_status(self):  # pragma: no cover - must not be hit
                raise AssertionError("raise_for_status should not be reached")

            def json(self):  # pragma: no cover - must not be hit
                return {}

        def fake_get(*args, **kwargs):
            calls["count"] += 1
            return _Resp()

        with patch.object(transport_mod.requests, "get", side_effect=fake_get):
            with pytest.raises(RuntimeError, match="authentication failed"):
                transport_mod.RequestsTransport().get_json(
                    "https://example.test/api", {"page": 1}
                )

        assert calls["count"] == 1

    def test_offset_source_pages_past_short_pages_until_empty(self):
        # ADB returns ~85 records for a size=100 window (the server filters
        # after slicing), so a short page is NOT the last page. An offset source
        # must keep paging until a truly empty page -- unlike page-number
        # sources, it must not stop on the first short page.
        task = _task(
            {
                "type": "offset",
                "offset_param": "offset",
                "size_param": "size",
                "page_size": 100,
                "start_page": 0,
            },
            items_path="data",
        )

        pages = {
            0: {"data": [{"id": i} for i in range(83)]},
            100: {"data": [{"id": i} for i in range(87)]},
            200: {"data": []},
        }

        def fake_get_page(_task, query, _transport=None):
            return pages[query["offset"]]

        with patch.object(collector, "_get_page", side_effect=fake_get_page):
            result = list(collector._iter_pages(task, None))

        assert [len(page) for page in result] == [83, 87]

    def test_single_request_source_fetches_once(self):
        # The GPPB case: the endpoint ignores paging and always returns the
        # same non-empty list. With type: "none" the collector must fetch once
        # and stop -- NOT loop forever.
        task = _task({"type": "none"}, items_path="")

        full_list = [{"id": 1}, {"id": 2}]
        calls = {"count": 0}

        def fake_get_page(_task, query, _transport=None):
            calls["count"] += 1
            assert "page" not in query  # no page param for single-request
            return full_list

        with patch.object(collector, "_get_page", side_effect=fake_get_page):
            result = list(collector._iter_pages(task, None))

        assert result == [full_list]
        assert calls["count"] == 1

    def test_param_variants_fetch_each_and_concatenate(self):
        # The GPPB case: one source split into three category partitions. With
        # type: "none" each variant is a single fetch; all three are yielded in
        # order and concatenated into one snapshot.
        task = _task(
            {"type": "none"},
            items_path="",
            param_variants=[
                {"category": "BLACKLISTED_ENTITIES"},
                {"category": "PERMANENT_BLACKLISTED_ENTITIES"},
                {"category": "TEMPORARY_REMOVED_BLACKLISTED_ENTITIES"},
            ],
        )

        seen = []

        def fake_get_page(_task, query, _transport=None):
            seen.append(query["category"])
            return [{"cat": query["category"]}]

        with patch.object(collector, "_get_page", side_effect=fake_get_page):
            result = list(collector._iter_pages(task, None))

        assert seen == [
            "BLACKLISTED_ENTITIES",
            "PERMANENT_BLACKLISTED_ENTITIES",
            "TEMPORARY_REMOVED_BLACKLISTED_ENTITIES",
        ]
        assert result == [
            [{"cat": "BLACKLISTED_ENTITIES"}],
            [{"cat": "PERMANENT_BLACKLISTED_ENTITIES"}],
            [{"cat": "TEMPORARY_REMOVED_BLACKLISTED_ENTITIES"}],
        ]

    def test_no_variants_preserves_single_fetch(self):
        # BEFORE/AFTER guard: a source with no param_variants still does exactly
        # one fetch using its base params -- unchanged from prior behaviour.
        task = _task({"type": "none"}, items_path="", params={"category": "X"})

        calls = {"count": 0}

        def fake_get_page(_task, query, _transport=None):
            calls["count"] += 1
            assert query == {"category": "X"}
            return [{"id": 1}]

        with patch.object(collector, "_get_page", side_effect=fake_get_page):
            result = list(collector._iter_pages(task, None))

        assert result == [[{"id": 1}]]
        assert calls["count"] == 1
