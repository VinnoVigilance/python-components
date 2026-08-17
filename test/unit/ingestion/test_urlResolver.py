"""
Unit tests for the URL resolver.

The resolver turns a source config into the actual download URL. Real
resolution fetches a landing page over the network, so ``resolve_by_link_text``
is tested with ``requests.get`` patched out.
"""

from unittest.mock import MagicMock, patch

import pytest

from ingestion.urlResolver import interface, linkResolver

pytestmark = pytest.mark.unit


class TestResolveUrl:
    def test_static_url_when_no_resolver(self):
        # No url_resolver block -> the config's fixed url is returned as-is.
        config = {"url": "https://example.test/list.xml"}
        assert interface.resolve_url(config) == "https://example.test/list.xml"

    def test_link_text_dispatch(self):
        config = {
            "url": "https://example.test",
            "url_resolver": {
                "type": "link_text",
                "source_page_url": "https://example.test/downloads",
                "value": "Current Consolidated List",
            },
        }

        with patch.object(
            interface, "resolve_by_link_text", return_value="https://example.test/f.pdf"
        ) as resolve:
            result = interface.resolve_url(config)

        resolve.assert_called_once_with(
            source_page_url="https://example.test/downloads",
            link_text="Current Consolidated List",
        )
        assert result == "https://example.test/f.pdf"

    def test_unsupported_resolver_type_raises(self):
        config = {"url": "https://example.test", "url_resolver": {"type": "bogus"}}
        with pytest.raises(ValueError):
            interface.resolve_url(config)


class TestResolveByLinkText:
    @staticmethod
    def _response(html):
        response = MagicMock()
        response.text = html
        response.raise_for_status = MagicMock()
        return response

    def test_finds_link_by_text_and_makes_absolute(self):
        html = '<a href="/docs/list-2025.pdf">Current Consolidated List</a>'

        with patch.object(
            linkResolver.requests, "get", return_value=self._response(html)
        ):
            url = linkResolver.resolve_by_link_text(
                source_page_url="https://site.test/downloads",
                link_text="Current Consolidated List",
            )

        assert url == "https://site.test/docs/list-2025.pdf"

    def test_partial_case_insensitive_match(self):
        html = '<a href="https://cdn.test/a.xml">DOWNLOAD the Current List here</a>'

        with patch.object(
            linkResolver.requests, "get", return_value=self._response(html)
        ):
            url = linkResolver.resolve_by_link_text(
                source_page_url="https://site.test/p",
                link_text="current list",
            )

        assert url == "https://cdn.test/a.xml"

    def test_skips_matching_link_without_href(self):
        # First matching link has no href and must be skipped; the second wins.
        html = (
            '<a>Current List</a>'
            '<a href="/real.pdf">Current List</a>'
        )

        with patch.object(
            linkResolver.requests, "get", return_value=self._response(html)
        ):
            url = linkResolver.resolve_by_link_text(
                source_page_url="https://site.test/p",
                link_text="Current List",
            )

        assert url == "https://site.test/real.pdf"

    def test_raises_when_link_not_found(self):
        html = '<a href="/x">Something else</a>'

        with patch.object(
            linkResolver.requests, "get", return_value=self._response(html)
        ):
            with pytest.raises(ValueError):
                linkResolver.resolve_by_link_text(
                    source_page_url="https://site.test/p",
                    link_text="Missing Link",
                )
