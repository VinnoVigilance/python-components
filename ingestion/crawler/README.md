# Complete Crawler Folder

This folder intentionally contains the Scrapy Item, Item Pipeline and optional
middleware components required by the current shared crawler design.

## Components

- `items.py`
  - `AdverseMediaArticleItem`
  - `WatchlistRecordItem`
- `pipelines.py`
  - `AdverseMediaHtmlStoragePipeline`
  - `WatchlistJsonlPipeline`
- `middlewares/rawResponseStorageMiddleware.py`
  - `RawResponseStorageMiddleware`
- `spiders/genericArticleSpider.py`
  - `GenericArticleSpider`
- `spiders/genericWatchlistSpider.py`
  - `GenericWatchlistSpider`
- `crawlerService.py`
- `configLoader.py`
- `settings.py`

`GenericArticleSpider` yields `AdverseMediaArticleItem` objects.
`AdverseMediaHtmlStoragePipeline` saves one HTML file per article. The optional
raw-response middleware is retained for other spiders, but the article spider
does not activate it, so article HTML is not saved twice.

Adverse-media article HTML is stored using the article `source_record_id` as
the filename (sanitized only for filesystem safety), for example
`<source_record_id>.html`. Article field extraction is handled inside the
adverse-media crawler and does not use the Watchlist `parsing/htmlParser.py`.
