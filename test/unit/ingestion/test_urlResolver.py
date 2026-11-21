"""
Unit tests for the URL resolver.

The resolver turns a source config into the actual download URL. Real
resolution fetches a landing page over the network, so ``resolve_by_link_text``
is tested with ``requests.get`` patched out.
"""

from unittest.mock import MagicMock, patch
from datetime import date

import pytest
import requests

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

    def test_latest_year_page_dispatch(self):
        resolver = {"type": "latest_year_page", "page_url_template": "https://annual.test/{year}"}
        with patch.object(interface, "resolve_latest_year_page", return_value="https://annual.test/file.pdf") as resolve:
            assert interface.resolve_url({"url_resolver": resolver}) == "https://annual.test/file.pdf"
        resolve.assert_called_once_with(resolver)


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


class TestScopedLinkText:
    @pytest.mark.parametrize("filename", ["20250929_4.xlsx", "20300101_list.xlsx"])
    def test_dispatch_discovers_changing_file_and_inherits_headers(self, monkeypatch, filename):
        response = requests.Response()
        response.status_code = 200
        response.url = "https://source.test/downloads/page.html"
        response._content = (
            '<a href="/archive.xlsx">Foreign User List</a>'
            f'<div id="userlist"><a href="{filename}">外国ユーザーリスト</a></div>'
        ).encode("utf-8")
        response._content_consumed = True
        get = MagicMock(return_value=response)
        monkeypatch.setattr(linkResolver.requests, "get", get)
        cfg = {
            "download_headers": {"User-Agent": "Configured client"},
            "url_resolver": {
                "type": "link_text",
                "value": "外国ユーザーリスト",
                "source_page_url": "https://source.test/page",
                "link_selector": "#userlist a[href$='.xlsx']",
                "encoding": "utf-8",
            },
        }
        assert interface.resolve_url(cfg) == f"https://source.test/{filename}"
        get.assert_called_once_with(
            "https://source.test/page", headers=cfg["download_headers"], timeout=30,
        )
        assert response.encoding == "utf-8"

    @pytest.mark.parametrize("html", [
        "", '<div><a href="a.xlsx">List</a><a href="b.xlsx">List</a></div>',
        '<div><a>List</a></div>',
    ])
    def test_missing_or_ambiguous_scoped_link_is_rejected(self, monkeypatch, html):
        response = MagicMock(text=html)
        monkeypatch.setattr(linkResolver.requests, "get", MagicMock(return_value=response))
        with pytest.raises(ValueError):
            linkResolver.resolve_by_link_text(
                "https://source.test/page", "List", link_selector="div a",
            )


class TestResolveLatestYearPage:
    @pytest.fixture
    def config(self, monkeypatch):
        clock = MagicMock()
        clock.today.return_value = date(2026, 6, 1)
        monkeypatch.setattr(linkResolver, "date", clock)
        return {
            "page_url_template": "https://annual.test/editions/{year}",
            "page_heading_template": "Public Register {year}",
            "first_year": 2025,
            "years_ahead": 1,
            "download_path_suffix": ".pdf",
            "missing_redirect_url": "https://annual.test/",
            "timeout": 61,
        }

    @staticmethod
    def response(html="", status=200, url="https://annual.test/editions/2027", history=()):
        response = requests.Response()
        response.status_code = status
        response.url = url
        response._content = html.encode()
        response.history = list(history)
        return response

    def test_searches_newest_first_and_skips_only_confirmed_missing_pages(self, config, monkeypatch):
        redirect = self.response(status=302)
        get = MagicMock(side_effect=[
            self.response(status=404),
            self.response(url="https://annual.test/", history=[redirect]),
            self.response('<h1>Public Register 2025</h1><a href="/files/latest.pdf">Download</a>',
                          url="https://annual.test/editions/2025"),
        ])
        monkeypatch.setattr(linkResolver.requests, "get", get)
        assert linkResolver.resolve_latest_year_page(config) == "https://annual.test/files/latest.pdf"
        assert [call.args[0] for call in get.call_args_list] == [
            f"https://annual.test/editions/{year}" for year in (2027, 2026, 2025)
        ]
        assert all(call.kwargs == {"timeout": 61} for call in get.call_args_list)

    @pytest.mark.parametrize("suffix", [".pdf", ".xlsx"])
    def test_configurable_extension_nested_heading_and_duplicate_links(self, config, monkeypatch, suffix):
        config["download_path_suffix"] = suffix
        html = (f'<h2> Public <span>Register</span>  2027 </h2>'
                f'<a href="/files/register{suffix.upper()}#first">Download</a>'
                f'<a href="/files/register{suffix.upper()}#second">Same file</a>'
                '<a href="/other.txt">Other file</a>')
        get = MagicMock(return_value=self.response(html))
        monkeypatch.setattr(linkResolver.requests, "get", get)
        assert linkResolver.resolve_latest_year_page(config) == f"https://annual.test/files/register{suffix.upper()}"
        get.assert_called_once()

    @pytest.mark.parametrize("links,count", [
        ('<a href="/readme.txt">Other</a>', 0),
        ('<a href="javascript:download.pdf">Invalid scheme</a>', 0),
        ('<a href="/one.pdf">One</a><a href="/two.pdf">Two</a>', 2),
    ])
    def test_missing_or_ambiguous_download_fails_without_trying_an_older_edition(self, config, monkeypatch, links, count):
        get = MagicMock(return_value=self.response('<h1>Public Register 2027</h1>' + links))
        monkeypatch.setattr(linkResolver.requests, "get", get)
        with pytest.raises(ValueError, match=f"found {count}"):
            linkResolver.resolve_latest_year_page(config)
        get.assert_called_once()

    def test_wrong_edition_heading_fails_instead_of_accepting_any_pdf(self, config, monkeypatch):
        get = MagicMock(return_value=self.response('<h1>Public Register 2026</h1><a href="/file.pdf">Download</a>'))
        monkeypatch.setattr(linkResolver.requests, "get", get)
        with pytest.raises(ValueError, match="Expected edition heading"):
            linkResolver.resolve_latest_year_page(config)
        get.assert_called_once()

    @pytest.mark.parametrize("history", [(), (301,), (302, 302)])
    def test_unexpected_homepage_response_is_not_treated_as_a_missing_year(self, config, monkeypatch, history):
        redirects = [self.response(status=status) for status in history]
        get = MagicMock(return_value=self.response('<h1>Homepage</h1>', url="https://annual.test/", history=redirects))
        monkeypatch.setattr(linkResolver.requests, "get", get)
        with pytest.raises(ValueError, match="Expected edition heading"):
            linkResolver.resolve_latest_year_page(config)
        get.assert_called_once()

    @pytest.mark.parametrize("error", [requests.Timeout("slow source"), requests.ConnectionError("offline")])
    def test_network_failure_does_not_silently_select_an_older_edition(self, config, monkeypatch, error):
        get = MagicMock(side_effect=error)
        monkeypatch.setattr(linkResolver.requests, "get", get)
        with pytest.raises(type(error)):
            linkResolver.resolve_latest_year_page(config)
        get.assert_called_once()

    def test_server_error_does_not_silently_select_an_older_edition(self, config, monkeypatch):
        get = MagicMock(return_value=self.response(status=503))
        monkeypatch.setattr(linkResolver.requests, "get", get)
        with pytest.raises(requests.HTTPError):
            linkResolver.resolve_latest_year_page(config)
        get.assert_called_once()

    def test_exhausted_year_range_raises_and_uses_default_timeout(self, config, monkeypatch):
        config.pop("timeout")
        get = MagicMock(return_value=self.response(status=404))
        monkeypatch.setattr(linkResolver.requests, "get", get)
        with pytest.raises(FileNotFoundError, match="2025 through 2027"):
            linkResolver.resolve_latest_year_page(config)
        assert get.call_count == 3
        assert all(call.kwargs == {"timeout": 30} for call in get.call_args_list)
