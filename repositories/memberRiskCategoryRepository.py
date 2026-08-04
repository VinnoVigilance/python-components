"""
Data access for the Member Risk Category ETL.

Reads members from ``core.watchlist_member`` and the daily change actions from
``delivery.watchlist_daily_delta_actions``, and maintains the SCD Type 2 history
in ``core.member_risk_category`` (expire-then-insert, one active row per member).
"""

from typing import Any

from psycopg2.extras import Json


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------

def find_max_effective_date(cursor) -> Any | None:
    """The latest delta batch date; the incremental ETL's default scope."""
    cursor.execute(
        """
        SELECT MAX(effective_date)
        FROM delivery.watchlist_daily_delta_actions
        """
    )

    row = cursor.fetchone()

    return row[0] if row else None


def find_delta_actions(
    cursor,
    effective_date: Any,
) -> list[dict[str, Any]]:
    """All ADD / UPDATE / DELETE actions for one effective date."""
    cursor.execute(
        """
        SELECT
            action,
            vv_member_id,
            watchlist_member_id
        FROM delivery.watchlist_daily_delta_actions
        WHERE effective_date = %s
        ORDER BY id
        """,
        (effective_date,),
    )

    return [
        {
            "action": row[0],
            "vv_member_id": row[1],
            "watchlist_member_id": row[2],
        }
        for row in cursor.fetchall()
    ]


# ---------------------------------------------------------------------------
# Watchlist member reads
# ---------------------------------------------------------------------------

def find_current_members_batch(
    cursor,
    last_member_id: int = 0,
    batch_size: int = 1000,
    list_name: str | None = None,
) -> list[dict[str, Any]]:
    """Keyset-paginated batch of current members, for the Initial Load.

    Ordered by ``id`` so the caller can page with ``last_member_id`` exactly the
    way ``rawPayloadRepository.find_raw_payload_batch`` does.

    ``list_name`` (optional): restrict the batch to members of ONE source list,
    matched on ``full_payload -> Sources[].ListName`` -- the same list identity
    the engine classifies on -- with a JSONB containment (``@>``) filter. When
    NULL, the guard passes and every current member is returned (all lists),
    exactly as before.
    """
    cursor.execute(
        """
        SELECT
            id,
            vv_member_id,
            version_no,
            full_payload
        FROM core.watchlist_member
        WHERE is_current = TRUE
          AND id > %(last_member_id)s
          AND (
              %(list_name)s IS NULL
              OR full_payload @> jsonb_build_object(
                  'Sources',
                  jsonb_build_array(
                      jsonb_build_object('ListName', %(list_name)s)
                  )
              )
          )
        ORDER BY id
        LIMIT %(batch_size)s
        """,
        {
            "last_member_id": last_member_id,
            "list_name": list_name,
            "batch_size": batch_size,
        },
    )

    return [
        {
            "id": row[0],
            "vv_member_id": row[1],
            "version_no": row[2],
            "full_payload": row[3],
        }
        for row in cursor.fetchall()
    ]


def find_member_by_id(
    cursor,
    watchlist_member_id: int,
) -> dict[str, Any] | None:
    """Fetch one watchlist member by its physical id (delta target)."""
    cursor.execute(
        """
        SELECT
            id,
            vv_member_id,
            version_no,
            full_payload
        FROM core.watchlist_member
        WHERE id = %s
        """,
        (watchlist_member_id,),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "vv_member_id": row[1],
        "version_no": row[2],
        "full_payload": row[3],
    }


# ---------------------------------------------------------------------------
# Risk category history (SCD Type 2)
# ---------------------------------------------------------------------------

def find_current_risk(
    cursor,
    vv_member_id: Any,
) -> dict[str, Any] | None:
    """The member's current active risk classification, locked for update.

    The one-active-row invariant means at most one row is expected; ``LIMIT 1``
    is defensive. ``FOR UPDATE`` serializes concurrent ETL runs on this member.
    """
    cursor.execute(
        """
        SELECT
            id,
            risk_details_hash
        FROM core.member_risk_category
        WHERE vv_member_id = %s
          AND is_current = TRUE
        ORDER BY id DESC
        LIMIT 1
        FOR UPDATE
        """,
        (vv_member_id,),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "risk_details_hash": row[1],
    }


def expire_current_risk(
    cursor,
    vv_member_id: Any,
) -> int:
    """Close every active risk row for a member. Returns the number expired.

    Used both before inserting a new version and to service a DELETE action
    (where no new row follows).
    """
    cursor.execute(
        """
        UPDATE core.member_risk_category
        SET
            is_current = FALSE,
            valid_to = NOW()
        WHERE vv_member_id = %s
          AND is_current = TRUE
        """,
        (vv_member_id,),
    )

    return cursor.rowcount


def insert_risk(
    cursor,
    risk_data: dict[str, Any],
) -> int:
    """Insert a new active risk classification version and return its id.

    ``risk_data`` keys: vv_member_id, watchlist_member_id, version_no,
    risk_details (dict), risk_details_hash.
    """
    query_data = {
        **risk_data,
        "risk_details": Json(risk_data["risk_details"]),
    }

    cursor.execute(
        """
        INSERT INTO core.member_risk_category (
            vv_member_id,
            watchlist_member_id,
            version_no,
            risk_details,
            risk_details_hash,
            valid_from,
            valid_to,
            is_current
        )
        VALUES (
            %(vv_member_id)s,
            %(watchlist_member_id)s,
            %(version_no)s,
            %(risk_details)s,
            %(risk_details_hash)s,
            NOW(),
            NULL,
            TRUE
        )
        RETURNING id
        """,
        query_data,
    )

    return cursor.fetchone()[0]
