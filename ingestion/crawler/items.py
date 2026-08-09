"""Data contracts passed from crawler spiders to Scrapy pipelines."""

import scrapy


class AdverseMediaArticleItem(scrapy.Item):
    """One downloaded adverse-media article before raw HTML storage."""

    article_number = scrapy.Field()
    article_url = scrapy.Field()
    discovery_url = scrapy.Field()
    discovery = scrapy.Field()
    article = scrapy.Field()
    source_record_id = scrapy.Field()
    html = scrapy.Field()
    raw_file_path = scrapy.Field()


class WatchlistRecordItem(scrapy.Item):
    """One structured Watchlist record; kept separate from adverse media."""

    external_id = scrapy.Field()
    payload = scrapy.Field()
