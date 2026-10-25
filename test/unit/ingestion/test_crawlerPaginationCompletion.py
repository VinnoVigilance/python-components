import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ingestion.crawler.crawler import crawl_source
from ingestion.crawler.models import CrawlerTask


class TestCrawlerPaginationCompletion(unittest.TestCase):
    def run_crawler(
        self, *, pagination=None, completed=False,
        error=None, detail_items=None, saved_html=False, media=False
    ):
        config = {"fetch_strategy": "direct"}
        if pagination is not None:
            config["pagination"] = pagination
        if saved_html:
            config["fetch_strategy"] = "saved_html"
        if media:
            config["acquisition"] = {
                "spider": "media",
                "fetch_strategy": "direct",
            }

        process = Mock()
        settings = Mock()

        def finish():
            state = process.crawl.call_args.kwargs.get("pagination_state")
            if state is not None:
                state["complete"] = completed
                state["error"] = error

        process.start.side_effect = finish

        with tempfile.TemporaryDirectory() as directory:
            saved_file = Path(directory) / "listing.html"
            saved_file.write_text("<html><body>Saved</body></html>")
            task = CrawlerTask(
                url="https://example.test/list",
                source_name="TEST",
                list_name="TEST-LIST",
                source_config=config,
                download_dir=directory,
                detail_items=detail_items,
                source_file_path=str(saved_file) if saved_html else None,
            )
            with (
                patch(
                    "ingestion.crawler.crawler.CrawlerProcess",
                    return_value=process,
                ),
                patch(
                    "ingestion.crawler.crawler.get_project_settings",
                    return_value=settings,
                ),
            ):
                result = crawl_source(task)
        return result, process

    def test_incomplete_pagination_raises(self):
        with self.assertRaisesRegex(RuntimeError, "final listing page"):
            self.run_crawler(pagination={"type": "next_link"})

    def test_listing_error_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            self.run_crawler(
                pagination={"type": "next_link"},
                error="https://example.test/list?page=1: HTTP 503",
            )

    def test_error_cannot_be_overridden_by_complete_flag(self):
        with self.assertRaisesRegex(RuntimeError, "Pagination loop"):
            self.run_crawler(
                pagination={"type": "next_link"},
                completed=True,
                error="Pagination loop",
            )

    def test_complete_pagination_returns_result(self):
        result, process = self.run_crawler(
            pagination={"type": "next_link"}, completed=True
        )
        self.assertEqual(result.record_count, 0)
        self.assertTrue(
            process.crawl.call_args.kwargs["pagination_state"]["complete"]
        )

    def test_no_pagination_keeps_existing_arguments(self):
        _, process = self.run_crawler()
        self.assertNotIn(
            "pagination_state", process.crawl.call_args.kwargs
        )

    def test_query_params_keeps_existing_behavior(self):
        _, process = self.run_crawler(
            pagination={
                "type": "query_params",
                "params": {"items_per_page": "All"},
            }
        )
        self.assertNotIn(
            "pagination_state", process.crawl.call_args.kwargs
        )

    def test_saved_html_does_not_require_live_pagination(self):
        _, process = self.run_crawler(
            pagination={"type": "next_link"}, saved_html=True
        )
        self.assertNotIn(
            "pagination_state", process.crawl.call_args.kwargs
        )

    def test_detail_only_retry_does_not_repeat_listing(self):
        _, process = self.run_crawler(
            pagination={"type": "next_link"},
            detail_items=[{
                "record_id": "1",
                "detail_url": "https://example.test/detail/1",
                "list_data": {},
            }],
        )
        self.assertNotIn(
            "pagination_state", process.crawl.call_args.kwargs
        )

    def test_media_keeps_its_existing_completion_policy(self):
        _, process = self.run_crawler(
            pagination={"type": "next_link"}, media=True
        )
        self.assertNotIn(
            "pagination_state", process.crawl.call_args.kwargs
        )


if __name__ == "__main__":
    unittest.main()

