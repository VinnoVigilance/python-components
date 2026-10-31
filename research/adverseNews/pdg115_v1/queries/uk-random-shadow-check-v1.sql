-- PDG-115 read-only, reproducible UK_GOV evaluation export.
-- Export the result with headers as UTF-8 CSV, keeping media_payload intact.
-- Two non-overlapping random groups: 60 from all saved UK_GOV articles and
-- 60 published in the most recent 12 months of this fixed 2026-10-31 study.
-- Selection does not use article text, URL section, model score, or label.
-- Some exported rows may overlap earlier local data; remove those locally
-- before any fresh blinded review, without changing this query.

WITH latest AS (
    SELECT DISTINCT ON (m.vv_media_id)
        m.id AS media_record_id,
        m.vv_media_id,
        m.version_no,
        m.media_payload::jsonb AS media_payload
    FROM core.media_record AS m
    JOIN common.lkup_media_dataset AS d ON d.id = m.dataset_id
    WHERE d.name = 'UK_GOV_NEWS_COMMUNICATIONS'
      AND m.vv_media_id IS NOT NULL
    ORDER BY m.vv_media_id, m.version_no DESC NULLS LAST, m.id DESC
), dated AS (
    SELECT
        l.*,
        CASE
            WHEN pub.full_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
            THEN left(pub.full_date, 10)::date
            ELSE NULL
        END AS published_date,
        src.source_url,
        l.media_payload #>> '{Publisher,Name}' AS publisher_name
    FROM latest AS l
    LEFT JOIN LATERAL (
        SELECT NULLIF(x.item ->> 'FullDate', '') AS full_date
        FROM jsonb_array_elements(
            CASE jsonb_typeof(l.media_payload -> 'Dates')
                WHEN 'array' THEN l.media_payload -> 'Dates'
                WHEN 'object' THEN jsonb_build_array(l.media_payload -> 'Dates')
                ELSE '[]'::jsonb
            END
        ) WITH ORDINALITY AS x(item, position)
        WHERE lower(x.item ->> 'Type') = 'published'
        ORDER BY (NULLIF(x.item ->> 'FullDate', '') IS NULL), x.position
        LIMIT 1
    ) AS pub ON TRUE
    LEFT JOIN LATERAL (
        SELECT NULLIF(x.item ->> 'SourceURL', '') AS source_url
        FROM jsonb_array_elements(
            CASE jsonb_typeof(l.media_payload -> 'Sources')
                WHEN 'array' THEN l.media_payload -> 'Sources'
                WHEN 'object' THEN jsonb_build_array(l.media_payload -> 'Sources')
                ELSE '[]'::jsonb
            END
        ) WITH ORDINALITY AS x(item, position)
        WHERE NULLIF(x.item ->> 'SourceURL', '') IS NOT NULL
        ORDER BY x.position
        LIMIT 1
    ) AS src ON TRUE
), all_time AS (
    SELECT * FROM dated
    ORDER BY md5('pdg115-all-time-v1|' || vv_media_id::text)
    LIMIT 60
), recent AS (
    SELECT d.* FROM dated AS d
    WHERE d.published_date >= DATE '2025-11-01'
      AND d.published_date <= DATE '2026-10-31'
      AND NOT EXISTS (
          SELECT 1 FROM all_time AS a WHERE a.vv_media_id = d.vv_media_id
      )
    ORDER BY md5('pdg115-recent-v1|' || d.vv_media_id::text)
    LIMIT 60
), combined AS (
    SELECT 'all_time' AS sample_arm, * FROM all_time
    UNION ALL
    SELECT 'recent_12_months' AS sample_arm, * FROM recent
)
SELECT
    sample_arm,
    media_record_id,
    vv_media_id,
    version_no,
    published_date,
    publisher_name,
    source_url,
    media_payload
FROM combined
ORDER BY sample_arm, vv_media_id;
