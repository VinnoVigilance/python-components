"""Shared Scrapy settings."""

BOT_NAME = "data_acquisition_crawler"
SPIDER_MODULES = ["ingestion.crawler.spiders"]
NEWSPIDER_MODULE = "ingestion.crawler.spiders"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_DELAY = 1.0
DOWNLOAD_TIMEOUT = 30

RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504, 522, 524]

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

USER_AGENT = "VigilanceDataAcquisition/1.0"

DOWNLOADER_MIDDLEWARES = {
    "ingestion.crawler.middlewares.rawResponseStorageMiddleware.RawResponseStorageMiddleware": 543,
}

FEED_EXPORT_ENCODING = "utf-8"
LOG_LEVEL = "INFO"
