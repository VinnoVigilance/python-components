"""
Browser-backed JSON transport.

Some JSON APIs sit behind a bot wall (Akamai, Cloudflare) that answers plain
``requests`` with a 403. A real browser clears that wall on first navigation
and keeps the cookies it was handed -- a "warm session". This transport opens
the stealth engine once, warms the session, then answers each ``get_json`` by
running the request *inside that warm page*, so it inherits the cookies the
wall expects.

It exposes the same tiny surface as ``RequestsTransport`` (``get_json`` + the
context-manager lifecycle), so the API collector's paging/merge recipe uses it
without knowing a browser is involved. It reuses ``StealthBrowserEngine`` as
is -- the engine is not modified.
"""

import json
import logging
import random
import time
from urllib.parse import urlencode

from ingestion.bypassCollector.engines.stealthBrowserEngine import (
    StealthBrowserEngine,
)

logger = logging.getLogger(__name__)


def _build_fetch_script(url, timeout_ms):
    """
    Build a self-contained async fetch expression for one URL, with a hard
    timeout so it can never hang.

    Two CDP-mode constraints shape this. First, the URL is *inlined* (JSON-
    encoded) rather than passed as an argument, because CDP's evaluate drops
    extra arguments (the old ``arguments[0]`` approach failed with "arguments is
    not defined"). Second, this is an **async** ``fetch`` inside an IIFE that
    yields a Promise -- run through ``evaluateAwait`` (``await_promise=True``).
    An earlier synchronous XHR hung the page indefinitely on a slow/stalled
    request; ``fetch`` with an ``AbortController`` guarantees the request is
    aborted after ``timeout_ms`` and a value always comes back. It runs inside
    the warm page, so it inherits the cookies the bot wall handed out. A failure
    (including the timeout abort) resolves to ``{status: 0, body: <error>}`` so
    the caller can fail fast rather than wait.
    """

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
    """
    Fetch JSON through a warm stealth-browser session.

    ``bypass_config`` keys used:
        warmup_url        page to navigate to first, to clear the wall
        headless          run the browser headless (default False)
        success_criteria  text proving the warm-up page loaded (optional)
        timeout_seconds   engine operation timeout (default 90)
        driver_version    chromedriver selector (default "mlatest" = match the
                          device's installed Chrome; rarely needs overriding)
        binary_location   explicit Chrome binary path (default None = auto)
        fetch_timeout_ms  per-request in-page fetch timeout (default 30000); a
                          request is aborted after this so it never hangs
        fetch_retries     transient-failure retries (default 6)
        fetch_retry_delay base backoff seconds (default 1.0)
        fetch_backoff     backoff growth factor per retry (default 2.0)
        fetch_max_delay   backoff cap in seconds (default 30.0)
        min_request_interval  min seconds between requests, anti-burst (default 0)
    """

    def __init__(self, bypass_config):
        self._cfg = bypass_config or {}
        self._engine = None
        self._fetch_timeout_ms = int(
            self._cfg.get("fetch_timeout_ms", 30000)
        )
        # A long run makes thousands of in-page fetches; some fail transiently
        # ("Failed to fetch" -- a blip, or the server shedding load / rate-
        # limiting a burst). Recover with EXPONENTIAL BACKOFF + jitter: wait
        # ``fetch_retry_delay``, then grow by ``fetch_backoff`` each attempt
        # (capped at ``fetch_max_delay``), with jitter so retrying requests
        # don't thunder back in lockstep. Generous by default, so even a
        # ~1-minute rate-limit self-heals instead of aborting the run. Auth
        # blocks (401/403) and real HTTP errors are NOT retried.
        self._fetch_retries = int(self._cfg.get("fetch_retries", 6))
        self._fetch_retry_delay = float(
            self._cfg.get("fetch_retry_delay", 1.0)
        )
        self._fetch_backoff = float(self._cfg.get("fetch_backoff", 2.0))
        self._fetch_max_delay = float(
            self._cfg.get("fetch_max_delay", 30.0)
        )
        # Proactive pacing: keep at least this many seconds between requests, so
        # a tight loop (e.g. the planner probing ~250 warrant countries) never
        # bursts the endpoint into rate-limiting in the first place. 0 = off.
        self._min_request_interval = float(
            self._cfg.get("min_request_interval", 0.0)
        )
        self._last_request_at = 0.0

    def __enter__(self):
        self._engine = StealthBrowserEngine(
            headless=self._cfg.get("headless", False),
            successCriteria=self._cfg.get("success_criteria", []),
            timeoutSeconds=self._cfg.get("timeout_seconds", 90),
            # Default "mlatest" -> match the driver to the device's Chrome, so
            # the same config runs on any machine. Override in bypass_config
            # only to pin an exact build or a fixed Chrome binary path.
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
        """
        Fetch and parse one JSON response from inside the warm page.
        """

        self._pace()

        full_url = _with_query(url, params)
        script = _build_fetch_script(full_url, self._fetch_timeout_ms)

        transient = None

        for attempt in range(1, self._fetch_retries + 1):
            # Log each in-page fetch so a long silent phase (e.g. planning, which
            # fires many "how many?" probes) is visibly progressing, and a
            # genuine hang is pinpointed to the exact URL.
            suffix = f" (attempt {attempt})" if attempt > 1 else ""
            logger.info(f"Browser fetch: {full_url}{suffix}")

            raw = self._engine.evaluateAwait(script)

            if not raw:
                # No value came back at all -- treat as transient and retry.
                transient = "no response from page"
            else:
                result = json.loads(raw)
                status = result.get("status")

                if status in (401, 403):
                    # An auth block will not fix itself on retry: fail hard.
                    raise RuntimeError(
                        f"Browser transport was blocked ({status}) for "
                        f"{full_url}. The bot wall rejected the request."
                    )

                if status == 0 or status >= 500:
                    # status 0 = the in-page fetch failed or timed out (a network
                    # blip / abort); 5xx = a server-side hiccup (overload / a
                    # momentary 500-503). Both are transient and usually succeed
                    # on retry, so back off and retry rather than aborting a
                    # multi-hour run on one bad response. A persistent 5xx still
                    # surfaces once the retries are exhausted.
                    transient = result.get("body") or f"HTTP {status}"
                elif not 200 <= status < 300:
                    # A real client-side HTTP error (404, 400, ...) will not fix
                    # itself on retry: fail fast.
                    raise RuntimeError(
                        f"Browser transport HTTP {status} for {full_url}"
                    )
                else:
                    return json.loads(result.get("body") or "null")

            # Fell through with a transient failure -- back off (exponentially,
            # with jitter) and retry.
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
        """
        Enforce ``min_request_interval`` between requests: sleep off any time
        remaining since the last request started, then stamp this one. Keeps a
        tight probe loop from bursting the endpoint. No-op when the interval is 0.
        """

        if self._min_request_interval > 0:
            wait = self._min_request_interval - (
                time.time() - self._last_request_at
            )

            if wait > 0:
                time.sleep(wait)

        self._last_request_at = time.time()

    def _backoff_delay(self, attempt):
        """
        Exponential backoff with equal jitter for retry ``attempt`` (1-based):
        ``min(base * backoff**(attempt-1), max_delay)``, then half fixed + half
        random so retries spread out instead of synchronising.
        """

        raw = self._fetch_retry_delay * (self._fetch_backoff ** (attempt - 1))
        capped = min(raw, self._fetch_max_delay)

        return capped / 2 + random.uniform(0, capped / 2)

    # NOTE: no ``get_json_many`` here on purpose. In-page concurrency needed the
    # async-callback script, which CDP mode does not support (``execute_async_
    # script`` has no CDP path). Rather than ship a broken parallel fetch, this
    # transport exposes only the synchronous ``get_json``; the collector detects
    # the absence of ``get_json_many`` and hydrates details sequentially (with
    # retry) through the same warm session. Restoring parallel hydration would
    # need a CDP-native async fetch -- a separate piece of work.


def _with_query(url, params):
    """
    Append query params to a URL. The in-page request cannot take a separate
    params argument, so the query must already be part of the URL string.
    """

    if not params:
        return url

    separator = "&" if "?" in url else "?"

    return f"{url}{separator}{urlencode(params)}"
