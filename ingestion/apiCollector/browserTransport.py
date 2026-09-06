"""Browser-backed JSON transport: opens the stealth engine once, warms the
session at ``warmup_url``, then runs each ``get_json`` inside that warm page so it
inherits the cookies a bot wall (Akamai/Cloudflare) expects. Same surface as
``RequestsTransport``. The engine is imported lazily."""

import json
import logging
import random
import time
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def _build_fetch_script(url, timeout_ms):
    """Async ``fetch`` expression for one URL, aborted after ``timeout_ms``; any
    failure resolves to ``{status: 0, body: <error>}``. The URL is inlined
    because CDP's evaluate drops extra arguments."""

    return (
        "(async () => {\n"
        "  const ctrl = new AbortController();\n"
        f"  const timer = setTimeout(() => ctrl.abort(), {int(timeout_ms)});\n"
        "  try {\n"
        f"    const r = await fetch({json.dumps(url)}, "
        "{headers: {'Accept': 'application/json'}, signal: ctrl.signal});\n"
        "    const t = await r.text();\n"
        "    return JSON.stringify({status: r.status, body: t});\n"
        "  } catch (e) {\n"
        "    return JSON.stringify({status: 0, body: String(e)});\n"
        "  } finally { clearTimeout(timer); }\n"
        "})()"
    )


class BrowserTransport:
    """Fetch JSON through a warm stealth-browser session.

    ``bypass_config`` keys: warmup_url, headless, success_criteria,
    timeout_seconds, driver_version, binary_location, fetch_timeout_ms,
    fetch_retries, fetch_retry_delay, fetch_backoff, fetch_max_delay,
    min_request_interval.
    """

    def __init__(self, bypass_config):
        self._cfg = bypass_config or {}
        self._engine = None
        self._fetch_timeout_ms = int(self._cfg.get("fetch_timeout_ms", 30000))
        self._fetch_retries = int(self._cfg.get("fetch_retries", 6))
        self._fetch_retry_delay = float(self._cfg.get("fetch_retry_delay", 1.0))
        self._fetch_backoff = float(self._cfg.get("fetch_backoff", 2.0))
        self._fetch_max_delay = float(self._cfg.get("fetch_max_delay", 30.0))
        self._min_request_interval = float(
            self._cfg.get("min_request_interval", 0.0)
        )
        self._last_request_at = 0.0

    def __enter__(self):
        from ingestion.bypassCollector.engines.stealthBrowserEngine import (
            StealthBrowserEngine,
        )

        self._engine = StealthBrowserEngine(
            headless=self._cfg.get("headless", False),
            successCriteria=self._cfg.get("success_criteria", []),
            timeoutSeconds=self._cfg.get("timeout_seconds", 90),
            driverVersion=self._cfg.get("driver_version", "mlatest"),
            binaryLocation=self._cfg.get("binary_location"),
        )
        self._engine.__enter__()

        warmup_url = self._cfg.get("warmup_url")

        if warmup_url:
            logger.info(f"Warming browser session at: {warmup_url}")

            if not self._engine.navigate(warmup_url):
                raise RuntimeError(
                    f"Browser transport could not warm up at {warmup_url}"
                )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._engine:
            self._engine.__exit__(exc_type, exc_val, exc_tb)

        return False

    def get_json(self, url, params=None):
        """Fetch and parse one JSON response from inside the warm page. Auth
        blocks (401/403) and real HTTP errors fail hard; transient failures
        (status 0/5xx) back off and retry."""

        self._pace()

        full_url = _with_query(url, params)
        script = _build_fetch_script(full_url, self._fetch_timeout_ms)

        transient = None

        for attempt in range(1, self._fetch_retries + 1):
            suffix = f" (attempt {attempt})" if attempt > 1 else ""
            logger.info(f"Browser fetch: {full_url}{suffix}")

            raw = self._engine.evaluateAwait(script)

            if not raw:
                transient = "no response from page"
            else:
                result = json.loads(raw)
                status = result.get("status")

                if status in (401, 403):
                    raise RuntimeError(
                        f"Browser transport was blocked ({status}) for "
                        f"{full_url}. The bot wall rejected the request."
                    )

                if status == 0 or status >= 500:
                    transient = result.get("body") or f"HTTP {status}"
                elif not 200 <= status < 300:
                    raise RuntimeError(
                        f"Browser transport HTTP {status} for {full_url}"
                    )
                else:
                    return json.loads(result.get("body") or "null")

            if attempt < self._fetch_retries:
                delay = self._backoff_delay(attempt)

                logger.warning(
                    f"Transient fetch failure ({transient}); backing off "
                    f"{delay:.1f}s before retry {attempt + 1}/"
                    f"{self._fetch_retries}"
                )

                time.sleep(delay)

        raise RuntimeError(
            f"Browser transport in-page fetch failed for {full_url} after "
            f"{self._fetch_retries} attempts: {transient}"
        )

    def _pace(self):
        """Keep at least ``min_request_interval`` between requests."""

        if self._min_request_interval > 0:
            wait = self._min_request_interval - (
                time.time() - self._last_request_at
            )

            if wait > 0:
                time.sleep(wait)

        self._last_request_at = time.time()

    def _backoff_delay(self, attempt):
        """Exponential backoff with equal jitter for retry ``attempt``."""

        raw = self._fetch_retry_delay * (self._fetch_backoff ** (attempt - 1))
        capped = min(raw, self._fetch_max_delay)

        return capped / 2 + random.uniform(0, capped / 2)


def _with_query(url, params):
    """Append query params to a URL (the in-page request needs them inlined)."""

    if not params:
        return url

    separator = "&" if "?" in url else "?"

    return f"{url}{separator}{urlencode(params)}"
