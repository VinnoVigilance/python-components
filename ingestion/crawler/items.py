"""Items transported through Scrapy pipelines."""

import scrapy


ARTICLE_GROUP_FIELDS = (
    "source",
    "urls",
    "classification",
    "content",
    "dates",
    "publisher",
    "taxonomy",
    "media",
    "files",
    "references",
    "links",
    "feed",
)


class ArticleItem(scrapy.Item):
    """Transport envelope for one nested adverse-media record."""

    record = scrapy.Field()
    record_key = scrapy.Field()
    raw_file_path = scrapy.Field()


class WatchlistRecordItem(scrapy.Item):
    """Transport envelope for one source-shaped watchlist record."""

    external_id = scrapy.Field()
    payload = scrapy.Field()
