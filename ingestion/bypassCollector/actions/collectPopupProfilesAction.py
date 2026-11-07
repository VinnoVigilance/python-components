import logging

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from ingestion.bypassCollector.actions.baseAction import BaseAction


logger = logging.getLogger(__name__)


class CollectPopupProfilesAction(BaseAction):
    def execute(self, actionConfig, context, engine) -> bool:
        try:
            driver = engine.sb.driver

            link_selector = actionConfig["link_selector"]
            profile_selector = actionConfig["profile_selector"]
            close_selector = actionConfig["close_selector"]
            container_id = actionConfig["container_id"]

            key_attribute = actionConfig.get("key_attribute", "href")
            required = actionConfig.get("required_selectors", [])
            minimum = int(actionConfig.get("minimum_count", 1))
            minimum_text = int(
                actionConfig.get("minimum_text_length", 0)
            )
            timeout = float(
                actionConfig.get("timeout_per_profile", 15)
            )
            click_mode = actionConfig.get("click_mode", "native")

            if minimum < 1 or timeout <= 0 or minimum_text < 0:
                raise ValueError("Invalid popup collection limits")

            if click_mode not in {"native", "javascript"}:
                raise ValueError(
                    f"Unsupported click mode: {click_mode}"
                )

            wait = WebDriverWait(driver, timeout)

            def discover_keys(current_driver):
                keys = []

                for node in current_driver.find_elements(
                    By.CSS_SELECTOR,
                    link_selector,
                ):
                    key = node.get_dom_attribute(key_attribute)

                    if not key:
                        raise ValueError(
                            f"Popup link is missing {key_attribute}"
                        )

                    if key not in keys:
                        keys.append(key)

                return keys if len(keys) >= minimum else False

            keys = wait.until(
                discover_keys,
                message=(
                    f"Expected at least {minimum} unique popup links"
                ),
            )

            logger.info(
                "Discovered %s unique popup links",
                len(keys),
            )

            wait.until(
                lambda d: not self._visible(d, profile_selector),
                message="A popup was already open before collection",
            )

            snapshots = []

            for index, key in enumerate(keys, start=1):
                logger.info(
                    "Loading popup %s/%s",
                    index,
                    len(keys),
                )

                link = wait.until(
                    lambda d: self._find_link(
                        d,
                        link_selector,
                        key_attribute,
                        key,
                    ),
                    message=f"Popup link disappeared: {key}",
                )

                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});",
                    link,
                )

                self._click(driver, link, click_mode)

                profile = wait.until(
                    lambda d: self._ready_profile(
                        d,
                        profile_selector,
                        required,
                        minimum_text,
                    ),
                    message=f"Popup content not ready: {key}",
                )

                snapshot = profile.get_attribute("outerHTML")

                if not snapshot:
                    raise ValueError("Popup HTML is empty")

                snapshots.append(snapshot)

                close = wait.until(
                    lambda d: self._single_visible(
                        d,
                        close_selector,
                    ),
                    message="Expected one visible popup close button",
                )

                self._click(driver, close, click_mode)

                wait.until(
                    lambda d: not self._visible(
                        d,
                        profile_selector,
                    ),
                    message=f"Popup did not close: {key}",
                )

                logger.info(
                    "Collected popup %s/%s",
                    index,
                    len(keys),
                )

            driver.execute_script(
                """
                document.getElementById(arguments[1])?.remove();

                const container = document.createElement("div");
                container.id = arguments[1];
                container.hidden = true;

                container.setAttribute(
                    "data-collection-complete", "true"
                );
                container.setAttribute(
                    "data-collected-count",
                    String(arguments[0].length)
                );

                container.innerHTML = arguments[0].join("");
                document.body.appendChild(container);
                """,
                snapshots,
                container_id,
            )

            context["popup_collection"] = {
                "expected": len(keys),
                "collected": len(snapshots),
            }

            logger.info(
                "Popup collection completed: %s/%s",
                len(snapshots),
                len(keys),
            )
            return True

        except Exception:
            logger.exception("Popup collection failed")
            return False

    @staticmethod
    def _find_link(driver, selector, attribute, key):
        matches = []

        for node in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if node.get_dom_attribute(attribute) == key:
                    if node.is_displayed():
                        return node

                    matches.append(node)

            except StaleElementReferenceException:
                continue

        return matches[0] if matches else False

    @staticmethod
    def _click(driver, element, mode):
        if mode == "javascript":
            driver.execute_script(
                "arguments[0].click();",
                element,
            )
        else:
            element.click()

    @staticmethod
    def _visible(driver, selector):
        nodes = []

        for node in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if node.is_displayed():
                    nodes.append(node)

            except StaleElementReferenceException:
                continue

        return nodes

    @classmethod
    def _single_visible(cls, driver, selector):
        nodes = cls._visible(driver, selector)
        return nodes[0] if len(nodes) == 1 else False

    @classmethod
    def _ready_profile(
        cls,
        driver,
        selector,
        required,
        minimum_text,
    ):
        try:
            profile = cls._single_visible(driver, selector)

            if not profile:
                return False

            if any(
                not profile.find_elements(By.CSS_SELECTOR, item)
                for item in required
            ):
                return False

            text = profile.get_attribute("textContent") or ""

            if len(text.strip()) < minimum_text:
                return False

            return profile

        except StaleElementReferenceException:
            return False