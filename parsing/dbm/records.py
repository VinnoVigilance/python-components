"""Expose printed DBM listings with the context required to interpret them."""

from copy import deepcopy


LISTING_COLLECTIONS = {
    "records": "listing",
    "representatives": "representative",
    "committees": "committee",
    "vacancies": "vacancy",
    "unlisted": "unlisted",
    "office_contacts": "office_contact",
    "unassigned_contacts": "unassigned_contact",
}
RECORD_METADATA = {"record_kind", "source_file", "source_path", "context"}


def _contains_listings(value):
    if isinstance(value, dict):
        return any(
            (key in LISTING_COLLECTIONS and isinstance(child, list) and bool(child))
            or _contains_listings(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_listings(child) for child in value)
    return False


def _walk_listings(node, path, context, source_file):
    metadata = {}
    children = []
    for key, value in node.items():
        if key in {"source", "pdf"} and value == source_file:
            continue
        if key in LISTING_COLLECTIONS and isinstance(value, list) and value:
            children.append((key, value, True))
        elif _contains_listings(value):
            children.append((key, value, False))
        else:
            metadata[key] = value

    if metadata:
        context = [*context, {"source_path": path, "fields": metadata}]

    for key, value, is_collection in children:
        child_path = [*path, key]
        if is_collection:
            for index, listing in enumerate(value):
                if not isinstance(listing, dict):
                    raise ValueError(f"Expected a DBM listing object at {child_path + [index]}")
                if RECORD_METADATA.intersection(listing):
                    raise ValueError(f"DBM listing metadata conflicts at {child_path + [index]}")
                kind = LISTING_COLLECTIONS[key]
                if key == "records" and "committee_lines" in listing:
                    kind = "committee"
                yield deepcopy({
                    **listing,
                    "record_kind": kind,
                    "source_path": [*child_path, index],
                    "context": context,
                })
        elif isinstance(value, dict):
            yield from _walk_listings(value, child_path, context, source_file)
        else:
            for index, child in enumerate(value):
                if not isinstance(child, dict):
                    raise ValueError(f"Expected a DBM section object at {child_path + [index]}")
                yield from _walk_listings(child, [*child_path, index], context, source_file)


def iter_listings(directory):
    """Yield raw source listings, preserving printed fields and ancestor evidence.

    Context entries contain the enclosing section's fields at their original
    source_path. This carries department headings, mission details, shared
    roles, group contacts, and review issues without joining or normalizing them.
    """
    if not isinstance(directory, dict) or not _contains_listings(directory):
        raise ValueError("DBM directory contains no source listings")
    yield from _walk_listings(directory, [], [], directory["source"])
