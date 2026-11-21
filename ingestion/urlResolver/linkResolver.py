from datetime import date
from urllib.parse import urljoin, urlsplit

import requests
from parsel import Selector


def resolve_by_link_text(
    source_page_url: str,
    link_text: str,
    timeout: int = 30,
    *,
    link_selector: str = "a",
    headers=None,
    encoding=None,
) -> str:

    request_options = {"timeout": timeout}
    if headers is not None:
        request_options["headers"] = headers
    response = requests.get(source_page_url, **request_options)

    response.raise_for_status()
    if encoding:
        response.encoding = encoding

    selector = Selector(
        text=response.text
    )

    expected_text = (
        link_text
        .strip()
        .lower()
    )

    links = selector.css(link_selector)
    if link_selector != "a" and (len(links) != 1 or not links[0].attrib.get("href")):
        raise ValueError("File selector must match exactly one download link.")

    for link in links:

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

def resolve_latest_year_page(cfg: dict) -> str:
    """Resolve one linked file from a predictable annual edition page.

    Check years newest first. Skip HTTP 404s and the configured missing-page
    redirect. The first remaining page must have the expected h1 or h2 heading
    and exactly one distinct link with the configured file suffix.
    """
    first_year = cfg["first_year"]
    last_year = date.today().year + cfg["years_ahead"]
    timeout = cfg.get("timeout", 30)
    suffix = cfg["download_path_suffix"].lower()
    missing_redirect_url = cfg.get("missing_redirect_url")

    for year in range(last_year, first_year - 1, -1):
        page_url = cfg["page_url_template"].format(year=year)
        response = requests.get(page_url, timeout=timeout)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        if (
            response.status_code == 200
            and response.url == missing_redirect_url
            and len(response.history) == 1
            and response.history[0].status_code == 302
        ):
            continue

        page = Selector(text=response.text)
        expected_heading = cfg["page_heading_template"].format(year=year)
        headings = [
            " ".join(heading.xpath("string(.)").get().split())
            for heading in page.css("h1, h2")
        ]
        if expected_heading not in headings:
            raise ValueError(
                f"Expected edition heading {expected_heading!r} at {page_url}"
            )

        links = set()
        for href in page.css("a::attr(href)").getall():
            link = urljoin(response.url, href)
            target = urlsplit(link)
            if (
                target.scheme in {"http", "https"}
                and target.path.lower().endswith(suffix)
            ):
                links.add(target._replace(fragment="").geturl())

        if len(links) != 1:
            raise ValueError(
                f"Expected one {suffix} download at {page_url}, found {len(links)}"
            )

        return links.pop()

    raise FileNotFoundError(
        f"No edition page found for years {first_year} through {last_year}."
    )


def resolve_json_list_filter(
    cfg: dict,
    logger=None,
) -> str:
    source_url = cfg["source_url"]
    timeout = int(cfg.get("timeout", 30))

    response = requests.get(
        source_url,
        timeout=timeout,
    )

    response.raise_for_status()

    payload = response.json()

    list_path = str(cfg.get("list_path", "")).strip()
    entries = payload

    if list_path:
        for key in list_path.split("."):
            if not isinstance(entries, dict):
                entries = None
                break
            entries = entries.get(key)

    if not isinstance(entries, list) or not entries:
        raise RuntimeError(
            "No entries found at list_path "
            f"'{list_path}' in {source_url}."
        )

    filter_field = cfg.get("filter_field", "mimeType")
    filter_value = cfg.get("filter_value")

    filtered = [
        entry for entry in entries
        if isinstance(entry, dict)
        and entry.get(filter_field) == filter_value
    ]

    if not filtered:
        raise RuntimeError(
            "No entry matched "
            f"{filter_field}={filter_value!r} "
            f"in {source_url}."
        )

    id_field = cfg.get("id_field", "id")

    filtered = [
        entry for entry in filtered
        if entry.get(id_field) is not None
    ]

    if not filtered:
        raise RuntimeError(
            "No matched entry carried "
            f"'{id_field}' in {source_url}."
        )

    sort_field = cfg.get("sort_field")

    selected = max(
        filtered,
        key=lambda entry: (
            _as_sortable(entry.get(id_field)),
            entry.get(sort_field) if sort_field else None,
        ),
    )

    resolved_url = cfg["url_template"].format(
        id=selected[id_field]
    )

    if logger:
        logger.info(
            "Resolved %s: id=%s url=%s",
            cfg.get("list_name", source_url),
            selected[id_field],
            resolved_url,
        )

    return resolved_url


def _as_sortable(value):
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))
