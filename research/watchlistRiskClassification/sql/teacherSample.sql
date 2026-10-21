WITH members AS (
    SELECT wm.full_payload AS p,
           wm.full_payload -> 'Sources' -> 0 ->> 'ListName' AS list_name,
           wm.full_payload -> 'Sources' -> 0 ->> 'SourceRecordId' AS source_record_id,
           wm.full_payload ->> 'EntityId' AS entity_id
    FROM core.watchlist_member wm
    WHERE wm.is_current
),
ranked AS (
    SELECT list_name, source_record_id, entity_id, p,
           row_number() OVER (PARTITION BY list_name
                              ORDER BY md5(coalesce(source_record_id, '') || '|' || coalesce(entity_id, '') || '|PDG-84')) AS rn,
           count(*) OVER (PARTITION BY list_name) AS list_size
    FROM members
)
SELECT list_name, source_record_id, entity_id, list_size, rn, p::text AS payload
FROM ranked
WHERE rn <= 1000
ORDER BY list_name, rn;
