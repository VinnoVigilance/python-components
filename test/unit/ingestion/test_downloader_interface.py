"""
Unit tests for the downloader.

Real downloading hits live government websites, which is slow and unreliable,
so a unit test must never actually reach the network. Here we:

  1. Test the DownloadTask model directly (pure data, no network).
  2. Test that download() hands the task to the real downloader and returns its
     result -- with the network call itself replaced by a fake (patched).

An actual end-to-end download against the real sites belongs in a separate
`network`-marked test that is skipped by default.
"""

from unittest.mock import patch

import pytest

from ingestion.downloader import interface
from ingestion.downloader.models import DownloadTask

pytestmark = pytest.mark.unit


class TestDownloadTask:
    def test_defaults(self):
        task = DownloadTask(
            url="https://example.com/list.xml",
            source_name="OFAC",
            list_name="SDN",
        )
        assert task.timeout == 30
        assert task.retry == 3
        assert task.headers == {}
        assert task.download_dir is None

    def test_headers_are_independent_between_instances(self):
        # A shared mutable default would leak headers between tasks; the model
        # uses field(default_factory=dict) to prevent that. Guard it here.
        a = DownloadTask(url="u", source_name="s", list_name="l")
        b = DownloadTask(url="u", source_name="s", list_name="l")
        a.headers["x"] = "1"
        assert b.headers == {}


def test_download_delegates_to_structured_downloader():
    task = DownloadTask(
        url="https://example.com/list.xml",
        source_name="OFAC",
        list_name="SDN",
    )

    with patch.object(
        interface, "download_file", return_value="/tmp/list.xml"
    ) as download_file:
        result = interface.download(task)

    download_file.assert_called_once_with(task)
    assert result == "/tmp/list.xml"
