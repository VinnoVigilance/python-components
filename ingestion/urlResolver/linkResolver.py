from urllib.parse import urljoin

import requests
from parsel import Selector


def resolve_by_link_text(
    source_page_url: str,
    link_text: str,
    timeout: int = 30,
) -> str:

    response = requests.get(
        source_page_url,
        timeout=timeout,
    )

    response.raise_for_status()

    selector = Selector(
        text=response.text
    )

    expected_text = (
        link_text
        .strip()
        .lower()
    )

    for link in selector.css("a"):

        current_text = " ".join(
            link.css("::text").getall()
        )

        current_text = (
            current_text
            .strip()
            .lower()
        )

        href = link.attrib.get("href")

        if not href:
            continue

        if expected_text in current_text:
            return urljoin(
                source_page_url,
                href,
            )

    raise ValueError(
        f"Download link not found: "
        f"{link_text}"
    )