from typing import Any

from psycopg2.extras import Json


def find_entity_type_id(
    cursor,
    entity_type_name: str,
) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM common.lkup_entity_type
        WHERE name = %s
        """,
        (entity_type_name,),
    )

    row = cursor.fetchone()

    return row[0] if row else None


def find_current_member(
    cursor,
    source_id: int,
    list_type_id: int,
    external_id: str,
) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT
            id,
            vv_member_id,
            version_no,
            record_hash
        FROM core.watchlist_member
        WHERE source_id = %s
          AND list_type_id = %s
          AND external_id = %s
          AND is_current = TRUE
        ORDER BY version_no DESC
        LIMIT 1
        FOR UPDATE
        """,
        (
            source_id,
            list_type_id,
            external_id,
        ),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "vv_member_id": row[1],
        "version_no": row[2],
        "record_hash": row[3],
    }


def insert_new_member(
    cursor,
    member_data: dict[str, Any],
) -> dict[str, Any]:
    query_data = {
        **member_data,
        "full_payload": Json(
            member_data["full_payload"]
        ),
    }

    cursor.execute(
        """
        INSERT INTO core.watchlist_member (
            raw_file_id,
            raw_member_id,
            source_id,
            list_type_id,
            external_id,
            entity_type_id,
            version_no,
            is_current,
            record_hash,
            valid_from,
            valid_to,
            change_type,
            full_payload
        )
        VALUES (
            %(raw_file_id)s,
            %(raw_member_id)s,
            %(source_id)s,
            %(list_type_id)s,
            %(external_id)s,
            %(entity_type_id)s,
            1,
            TRUE,
            %(record_hash)s,
            NOW(),
            NULL,
            'NEW',
            %(full_payload)s
        )
        RETURNING
            id,
            vv_member_id,
            version_no
        """,
        query_data,
    )

    row = cursor.fetchone()

    return {
        "id": row[0],
        "vv_member_id": row[1],
        "version_no": row[2],
    }


def close_current_member(
    cursor,
    core_member_id: int,
) -> None:
    cursor.execute(
        """
        UPDATE core.watchlist_member
        SET
            is_current = FALSE,
            valid_to = NOW()
        WHERE id = %s
          AND is_current = TRUE
        """,
        (core_member_id,),
    )

    if cursor.rowcount != 1:
        raise RuntimeError(
            "Current core member could not be closed. "
            f"Core member ID: {core_member_id}"
        )


def insert_updated_member(
    cursor,
    member_data: dict[str, Any],
    vv_member_id: Any,
    version_no: int,
) -> dict[str, Any]:
    query_data = {
        **member_data,
        "vv_member_id": vv_member_id,
        "version_no": version_no,
        "full_payload": Json(
            member_data["full_payload"]
        ),
    }

    cursor.execute(
        """
        INSERT INTO core.watchlist_member (
            raw_file_id,
            raw_member_id,
            source_id,
            vv_member_id,
            list_type_id,
            external_id,
            entity_type_id,
            version_no,
            is_current,
            record_hash,
            valid_from,
            valid_to,
            change_type,
            full_payload
        )
        VALUES (
            %(raw_file_id)s,
            %(raw_member_id)s,
            %(source_id)s,
            %(vv_member_id)s,
            %(list_type_id)s,
            %(external_id)s,
            %(entity_type_id)s,
            %(version_no)s,
            TRUE,
            %(record_hash)s,
            NOW(),
            NULL,
            'UPDATED',
            %(full_payload)s
        )
        RETURNING
            id,
            vv_member_id,
            version_no
        """,
        query_data,
    )

    row = cursor.fetchone()

    return {
        "id": row[0],
        "vv_member_id": row[1],
        "version_no": row[2],
    }

def find_deleted_current_members(
    cursor,
    source_id: int,
    list_type_id: int,
    watchlist_file_id: int,
) -> list[dict]:
    cursor.execute(
        """
        SELECT
            member.id,
            member.vv_member_id,
            member.source_id,
            member.list_type_id,
            member.external_id,
            member.entity_type_id,
            member.version_no,
            member.record_hash,
            member.full_payload
        FROM core.watchlist_member AS member
        WHERE member.source_id = %s
          AND member.list_type_id = %s
          AND member.is_current = TRUE
          AND member.change_type IS DISTINCT FROM 'DELETED'
          AND NOT EXISTS (
              SELECT 1
              FROM raw.unparsed_watchlist_payload AS raw_member
              WHERE raw_member.watchlist_file_id = %s
                AND raw_member.external_id = member.external_id
          )
        FOR UPDATE
        """,
        (
            source_id,
            list_type_id,
            watchlist_file_id,
        ),
    )

    return [
        {
            "id": row[0],
            "vv_member_id": row[1],
            "source_id": row[2],
            "list_type_id": row[3],
            "external_id": row[4],
            "entity_type_id": row[5],
            "version_no": row[6],
            "record_hash": row[7],
            "full_payload": row[8],
        }
        for row in cursor.fetchall()
    ]

def insert_deleted_member(
    cursor,
    current_member: dict,
    watchlist_file_id: int,
) -> int:
    cursor.execute(
        """
        INSERT INTO core.watchlist_member (
            raw_file_id,
            raw_member_id,
            vv_member_id,
            source_id,
            list_type_id,
            external_id,
            entity_type_id,
            version_no,
            is_current,
            record_hash,
            valid_from,
            valid_to,
            change_type,
            full_payload
        )
        VALUES (
            %s,
            NULL,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            TRUE,
            %s,
            CURRENT_TIMESTAMP,
            NULL,
            'DELETED',
            %s
        )
        RETURNING id
        """,
        (
            watchlist_file_id,
            current_member["vv_member_id"],
            current_member["source_id"],
            current_member["list_type_id"],
            current_member["external_id"],
            current_member["entity_type_id"],
            current_member["version_no"] + 1,
            current_member["record_hash"],
            # full_payload arrives as a dict (jsonb read back by find_deleted_
            # current_members); psycopg2 cannot adapt a bare dict, so wrap it.
            Json(current_member["full_payload"]),
        ),
    )

    return cursor.fetchone()[0]

def find_current_members_batch(
    cursor,
    last_member_id: int = 0,
    batch_size: int = 1000,
) -> list[dict[str, Any]]:
    """
    Return a keyset-paginated batch of current watchlist members.

    Used by downstream consumers such as Elasticsearch indexing.
    """

    cursor.execute(
        """
        SELECT
            id,
            vv_member_id,
            source_id,
            list_type_id,
            external_id,
            entity_type_id,
            version_no,
            is_current,
            record_hash,
            valid_from,
            valid_to,
            change_type,
            full_payload
        FROM core.watchlist_member
        WHERE is_current = TRUE
          AND id > %s
        ORDER BY id
        LIMIT %s
        """,
        (
            last_member_id,
            batch_size,
        ),
    )

    return [
        {
            "id": row[0],
            "vv_member_id": row[1],
            "source_id": row[2],
            "list_type_id": row[3],
            "external_id": row[4],
            "entity_type_id": row[5],
            "version_no": row[6],
            "is_current": row[7],
            "record_hash": row[8],
            "valid_from": row[9],
            "valid_to": row[10],
            "change_type": row[11],
            "full_payload": row[12],
        }
        for row in cursor.fetchall()
    ]