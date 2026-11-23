import json
import logging
from time import monotonic, sleep

from ingestion.bypassCollector.actions.baseAction import BaseAction


logger = logging.getLogger(__name__)


class ScrollUntilIdleAction(BaseAction):
    """Scroll a rendered listing until it stops yielding new links."""

    def execute(self, actionConfig, context, engine):
        link_selector = actionConfig["link_selector"]
        close_selector = actionConfig.get("overlay_close_selector")
        idle_seconds = float(actionConfig.get("idle_timeout_seconds", 45))
        poll_seconds = float(actionConfig.get("poll_interval_seconds", 0.75))
        max_seconds = actionConfig.get("max_scroll_seconds")
        if max_seconds is not None:
            max_seconds = float(max_seconds)

        if idle_seconds <= 0 or poll_seconds <= 0 or (
            max_seconds is not None and max_seconds <= 0
        ):
            raise ValueError("Scroll timeouts must be positive")

        read_script = (
            "Promise.resolve(JSON.stringify("
            f"Array.from(document.querySelectorAll({json.dumps(link_selector)}), "
            "link => link.href).filter(Boolean)))"
        )

        scroll_script = """
            Promise.resolve(((selector) => {
                if (selector) {
                    const close = Array.from(document.querySelectorAll(selector))
                        .find(button => button.getClientRects().length);
                    if (close) close.click();
                }
                window.scrollBy(0, Math.floor(window.innerHeight * 0.8));
                return true;
            })(CLOSE_SELECTOR))
        """.replace("CLOSE_SELECTOR", json.dumps(close_selector))

        def read_links():
            result = engine.evaluateAwait(read_script)
            if not isinstance(result, str):
                raise RuntimeError("Browser did not return the article links")
            urls = json.loads(result)
            if not isinstance(urls, list):
                raise RuntimeError("Browser did not return a list of links")
            return set(urls)

        seen = read_links()
        if not seen:
            logger.error("No initial links found for selector %s", link_selector)
            return False

        started = monotonic()
        idle_deadline = started + idle_seconds
        time_limit = started + max_seconds if max_seconds is not None else None

        while True:
            now = monotonic()
            if time_limit is not None and now >= time_limit:
                logger.info("Scroll reached its time limit | Observed links: %d", len(seen))
                return True
            if now >= idle_deadline:
                logger.info("Scroll stopped after inactivity | Observed links: %d", len(seen))
                return True

            if engine.evaluateAwait(scroll_script) is not True:
                raise RuntimeError("Browser could not scroll the listing")

            sleep(poll_seconds)
            fresh = read_links() - seen
            if fresh:
                seen.update(fresh)
                idle_deadline = monotonic() + idle_seconds
                logger.info("Scrolled listing: +%d | Total: %d", len(fresh), len(seen))