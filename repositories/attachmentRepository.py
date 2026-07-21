from typing import Any


def find_attachment_by_hash(
    cursor,
    file_hash: str,
) -> dict[str, Any] | None:
    cursor.execute(
        """
        SELECT
            id,
            storage_path,
            file_name,
            file_type,
            mime_type,
            file_size,
            file_hash,
            source_url
        FROM raw.attachment
        WHERE file_hash = %s
        """,
        (file_hash,),
    )

    row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "storage_path": row[1],
        "file_name": row[2],
        "file_type": row[3],
        "mime_type": row[4],
        "file_size": row[5],
        "file_hash": row[6],
        "source_url": row[7],
    }


def insert_attachment(
    cursor,
    attachment_data: dict[str, Any],
) -> int:
    cursor.execute(
        """
        INSERT INTO raw.attachment (
            storage_path,
            file_name,
            file_type,
            mime_type,
            file_size,
            file_hash,
            source_url
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        RETURNING id
        """,
        (
            attachment_data["storage_path"],
            attachment_data["file_name"],
            attachment_data["file_type"],
            attachment_data["mime_type"],
            attachment_data["file_size"],
            attachment_data["file_hash"],
            attachment_data.get("source_url"),
        ),
    )

    return cursor.fetchone()[0]


def find_list_attachment(
    cursor,
    raw_file_id: int,
    attachment_id: int,
) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM raw.list_attachment
        WHERE raw_file_id = %s
          AND attachment_id = %s
        """,
        (
            raw_file_id,
            attachment_id,
        ),
    )

    row = cursor.fetchone()

    return row[0] if row else None


def insert_list_attachment(
    cursor,
    raw_file_id: int,
    attachment_id: int,
) -> int:
    cursor.execute(
        """
        INSERT INTO raw.list_attachment (
            raw_file_id,
            attachment_id
        )
        VALUES (
            %s,
            %s
        )
        RETURNING id
        """,
        (
            raw_file_id,
            attachment_id,
        ),
    )

    return cursor.fetchone()[0]


def find_member_attachment(
    cursor,
    external_id: str,
    attachment_id: int,
    attachment_type: str,
) -> int | None:
    cursor.execute(
        """
        SELECT id
        FROM raw.member_attachment
        WHERE external_id = %s
          AND attachment_id = %s
          AND attachment_type = %s
        """,
        (
            external_id,
            attachment_id,
            attachment_type,
        ),
    )

    row = cursor.fetchone()

    return row[0] if row else None


def insert_member_attachment(
    cursor,
    external_id: str,
    attachment_id: int,
    attachment_type: str,
) -> int:
    cursor.execute(
        """
        INSERT INTO raw.member_attachment (
            external_id,
            attachment_id,
            attachment_type
        )
        VALUES (
            %s,
            %s,
            %s
        )
        RETURNING id
        """,
        (
            external_id,
            attachment_id,
            attachment_type,
        ),
    )

    return cursor.fetchone()[0]