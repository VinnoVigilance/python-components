from typing import Any


def build_watchlist_document(
    member: dict[str, Any],
) -> dict[str, Any]:
    """
    Build the Elasticsearch document for one canonical watchlist member.

    The canonical payload is preserved without restructuring.
    """

    return {
        "record_id": str(member["vv_member_id"]),
        "source_id": str(member["source_id"]),
        "list_type_id": str(member["list_type_id"]),
        "external_id": (
            str(member["external_id"])
            if member["external_id"] is not None
            else None
        ),
        "version_no": member["version_no"],
        "is_current": member["is_current"],
        "change_type": member["change_type"],
        "valid_from": member["valid_from"],
        "valid_to": member["valid_to"],
        "payload": member["full_payload"] or {},
    }