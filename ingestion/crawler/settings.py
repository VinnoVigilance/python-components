"""Shared settings for the crawler module."""

BOT_NAME = "data_acquisition_crawler"

SPIDER_MODULES = ["ingestion.crawler.spiders"]
NEWSPIDER_MODULE = "ingestion.crawler.spiders"

ROBOTSTXT_OBEY = True

# Network requests run concurrently; the final local JSONL pass is also parallel.
CONCURRENT_REQUESTS = 16
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_DELAY = 0.25
DOWNLOAD_TIMEOUT = 30

RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504, 522, 524]

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.5
AUTOTHROTTLE_MAX_DELAY = 30.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

USER_AGENT = "VigilanceDataAcquisition/1.0"
LOG_LEVEL = "INFO"

# Optional middleware remains available for spiders that set
# request.meta["save_raw_response"]. GenericArticleSpider does not set it.
DOWNLOADER_MIDDLEWARES = {
    (
        "ingestion.crawler.middlewares.rawResponseStorageMiddleware."
        "RawResponseStorageMiddleware"
    ): 543,
}
