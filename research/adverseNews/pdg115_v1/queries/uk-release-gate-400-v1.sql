-- PDG-115 read-only export for a fresh UK_GOV release-gate review.
-- Export all rows with headers as UTF-8 CSV; keep media_payload intact.
-- The query returns up to 500 recent records. Locally exclude every earlier
-- train/review article and unusable text, then freeze the first 400 remaining
-- in the deterministic sample order BEFORE reading labels or model scores.
-- The extra 100 allow for local overlaps/blank bodies without choosing
-- replacements based on model performance. This is evaluation data, not
-- training data. Use a fresh salt so it differs from the earlier v1 packet.

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
)
SELECT
    media_record_id,
    vv_media_id,
    version_no,
    published_date,
    publisher_name,
    source_url,
    media_payload
FROM dated
WHERE published_date >= DATE '2025-11-01'
  AND published_date <= DATE '2026-10-31'
ORDER BY md5('pdg115-uk-release-gate-400-v1|' || vv_media_id::text)
LIMIT 500;
