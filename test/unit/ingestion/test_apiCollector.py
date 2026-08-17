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

def _task(pagination, items_path="items"):
    return ApiCollectorTask(
        url="https://example.test/api",
        source_name="TEST",
        list_name="TEST",
        pagination=pagination,
        items_path=items_path,
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

        def fake_get_page(_task, query):
            return pages[query["page"]]

        with patch.object(collector, "_get_page", side_effect=fake_get_page):
            result = list(collector._iter_pages(task))

        assert result == [[{"id": 1}], [{"id": 2}]]

    def test_single_request_source_fetches_once(self):
        # The GPPB case: the endpoint ignores paging and always returns the
        # same non-empty list. With type: "none" the collector must fetch once
        # and stop -- NOT loop forever.
        task = _task({"type": "none"}, items_path="")

        full_list = [{"id": 1}, {"id": 2}]
        calls = {"count": 0}

        def fake_get_page(_task, query):
            calls["count"] += 1
            assert "page" not in query  # no page param for single-request
            return full_list

        with patch.object(collector, "_get_page", side_effect=fake_get_page):
            result = list(collector._iter_pages(task))

        assert result == [full_list]
        assert calls["count"] == 1
