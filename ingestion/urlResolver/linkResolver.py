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