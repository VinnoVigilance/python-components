import unittest

from ingestion.crawler.pagination import (
    PaginationError,
    resolve_next_page_url,
)


class TestCrawlerPagination(unittest.TestCase):
    URL = "https://example.test/alerts?page=1"

    def test_relative_next_page(self):
        self.assertEqual(
            resolve_next_page_url(self.URL, "?page=2"),
            "https://example.test/alerts?page=2",
        )

    def test_absolute_next_page(self):
        expected = "https://example.test/alerts?page=2"

        self.assertEqual(
            resolve_next_page_url(self.URL, expected),
            expected,
        )

    def test_absent_next_link_means_last_page(self):
        self.assertIsNone(
            resolve_next_page_url(self.URL, None)
        )

    def test_empty_link_is_an_error(self):
        for href in ("", "   "):
            with self.subTest(href=href):
                with self.assertRaises(PaginationError):
                    resolve_next_page_url(self.URL, href)

    def test_rejects_other_origins_and_schemes(self):
        links = (
            "https://other.test/alerts?page=2",
            "//other.test/alerts?page=2",
            "http://example.test/alerts?page=2",
            "javascript:void(0)",
            "mailto:test@example.test",
        )

        for href in links:
            with self.subTest(href=href):
                with self.assertRaises(PaginationError):
                    resolve_next_page_url(self.URL, href)

    def test_rejects_self_links(self):
        for href in ("?page=1", "#top", self.URL):
            with self.subTest(href=href):
                with self.assertRaises(PaginationError):
                    resolve_next_page_url(self.URL, href)

    def test_rejects_previously_visited_page(self):
        with self.assertRaises(PaginationError):
            resolve_next_page_url(
                self.URL,
                "?page=0",
                visited_urls=[
                    "https://example.test/alerts?page=0#top"
                ],
            )

    def test_preserves_query_and_removes_fragment(self):
        self.assertEqual(
            resolve_next_page_url(
                self.URL,
                "?category=warnings&page=2#results",
            ),
            "https://example.test/alerts?category=warnings&page=2",
        )


if __name__ == "__main__":
    unittest.main()