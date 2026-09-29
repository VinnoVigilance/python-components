"""Unit tests for GenericSpider record_id extraction strategies."""

import pytest

from ingestion.crawler.spiders.genericSpider import GenericSpider

pytestmark = pytest.mark.unit


def _spider(record_id_config):
    return GenericSpider(
        task=None,
        crawler_config={"record_id": record_id_config},
        storage=None,
        records=[],
    )


class TestExtractRecordId:
    def test_field_strategy_reads_scraped_field(self):
        spider = _spider({"strategy": "field", "source": "nid"})
        assert spider._extract_record_id("https://x/any", {"nid": "964"}) == "964"

    def test_field_strategy_missing_value_returns_none(self):
        spider = _spider({"strategy": "field", "source": "nid"})
        assert spider._extract_record_id("https://x/any", {}) is None

    def test_url_regex_strategy_still_works(self):
        spider = _spider({"strategy": "url_regex", "pattern": r"/([^/?#]+)$"})
        assert spider._extract_record_id("https://x/foo-bar", {}) == "foo-bar"

    def test_unknown_strategy_raises(self):
        spider = _spider({"strategy": "nope"})
        with pytest.raises(ValueError):
            spider._extract_record_id("https://x/any", {})
