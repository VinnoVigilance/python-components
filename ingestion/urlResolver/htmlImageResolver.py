"""Select one image URL from already-acquired HTML; performs no HTTP requests."""

import math
import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

from lxml import html as lxml_html


@dataclass(frozen=True)
class ResolvedImage:
    url: str
    attribute: str
    descriptor: str | None = None
    width_hint: int | None = None


def http_url(url, base_url):
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Image URL is empty.")
    resolved = urldefrag(urljoin(base_url, url.strip()))[0]
    parts = urlsplit(resolved)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Image URL must use HTTP or HTTPS.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Image URL must not contain credentials.")
    return resolved


def resolve_image_from_html(html_text, page_url, config):
    """Support src or largest uniform w/x srcset, with an exact-one selector.

    srcset URLs containing literal commas are deliberately rejected instead of
    guessed. Configure prefer='src' / attribute for those uncommon documents.
    """
    if not isinstance(html_text, str) or not html_text.strip():
        raise ValueError("Image discovery requires non-empty HTML.")
    if not isinstance(config, dict):
        raise ValueError("Image discovery config must be an object.")
    selector = config.get("selector")
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("Image discovery requires a selector.")
    root = lxml_html.fromstring(html_text)
    selector_type = config.get("selector_type", "xpath")
    if selector_type == "xpath":
        nodes = root.xpath(selector)
    elif selector_type == "css":
        nodes = root.cssselect(selector)
    else:
        raise ValueError("Image selector_type must be xpath or css.")
    if not isinstance(nodes, list) or len(nodes) != 1:
        raise ValueError("Image selector must match exactly one element.")
    node = nodes[0]
    if not hasattr(node, "get") or str(node.tag).lower() != "img":
        raise ValueError("Image selector must select an img element.")
    page_url = http_url(page_url, page_url)
    bases = root.xpath("//base[@href]/@href")
    base_url = http_url(bases[0], page_url) if bases else page_url
    preference = config.get("prefer", "largest_srcset")
    if preference not in {"src", "largest_srcset"}:
        raise ValueError("Image prefer must be src or largest_srcset.")
    srcset_attribute = config.get("srcset_attribute", "srcset")
    src_attribute = config.get("attribute", "src")
    raw = node.get(srcset_attribute)
    if preference == "largest_srcset" and raw and raw.strip():
        candidates, kind, used = [], None, set()
        for part in raw.split(","):
            pieces = part.split()
            if len(pieces) != 2:
                raise ValueError("Unsupported or malformed srcset candidate.")
            url, descriptor = pieces
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([wx])", descriptor)
            if not match:
                raise ValueError("srcset requires positive w or x descriptors.")
            amount, unit = float(match[1]), match[2]
            if not math.isfinite(amount) or amount <= 0 or (unit == "w" and not amount.is_integer()):
                raise ValueError("Invalid srcset descriptor.")
            if kind is not None and kind != unit:
                raise ValueError("Mixed width/density srcset descriptors are unsupported.")
            if amount in used:
                raise ValueError("Duplicate srcset descriptor.")
            kind = unit
            used.add(amount)
            candidates.append((amount, http_url(url, base_url), descriptor))
        amount, url, descriptor = max(candidates, key=lambda item: item[0])
        return ResolvedImage(url, srcset_attribute, descriptor, int(amount) if kind == "w" else None)
    return ResolvedImage(http_url(node.get(src_attribute), base_url), src_attribute)
