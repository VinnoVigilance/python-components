"""
Unit tests for the downloader's filename and path helpers.

``download_file`` itself performs a real network download, so it is not tested
here (that belongs in a network-marked test). These cover the pure helpers that
decide the saved file's name and its dated directory.
"""

import datetime
import hashlib

import pytest

from ingestion.downloader import structured_downloader as sd
from ingestion.downloader.models import DownloadTask

pytestmark = pytest.mark.unit


class TestOriginalFilename:
    def test_explicit_filename_wins(self):
        task = DownloadTask(
            url="https://x/y.csv", source_name="S", list_name="L", filename="custom.xml"
        )
        assert sd._get_original_filename(task) == "custom.xml"

    def test_derives_from_url_path(self):
        task = DownloadTask(url="https://x/path/list.xml", source_name="S", list_name="L")
        assert sd._get_original_filename(task) == "list.xml"

    def test_md5_fallback_when_no_path_segment(self):
        task = DownloadTask(url="https://x.test", source_name="S", list_name="L")
        expected = hashlib.md5(task.url.encode()).hexdigest()
        assert sd._get_original_filename(task) == expected


class TestFinalFilename:
    def test_uses_list_name_timestamp_and_extension(self):
        task = DownloadTask(url="https://x/list.csv", source_name="S", list_name="MYLIST")
        stamp = datetime.datetime(2026, 8, 17, 10, 30, 0)
        assert sd._build_final_filename(task, "list.csv", stamp) == "MYLIST_20260817_103000.csv"


class TestDownloadDirectory:
    def test_uses_task_dir_and_dated_layout(self, tmp_path):
        task = DownloadTask(
            url="https://x/y.csv",
            source_name="SRC",
            list_name="LST",
            download_dir=str(tmp_path),
        )
        directory = sd._build_download_directory(task, datetime.datetime(2026, 8, 17))
        assert directory == tmp_path / "SRC" / "LST" / "year=2026" / "month=08" / "day=17"
