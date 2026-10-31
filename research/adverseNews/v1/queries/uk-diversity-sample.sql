-- PDG-115: read-only UK_GOV sample across publication years and URL sections.
-- HISTORICAL QUERY: the path expression gives each non-/government/ medicine
-- article its own section. The 1,500-row export is useful for discovering
-- article types but is not representative of the dataset's section mix.
-- See results/uk-diversity-review-v1/REVIEW_REPORT.md before reusing it.
-- Export result with headers as UTF-8 CSV. The media_payload column is the
-- saved full JSON payload; keep it intact so title/body parsing can be checked.
-- This query samples at most 1,500 distinct logical articles; it does not
-- create labels or modify database records.
-- Run against the same PostgreSQL database as uk-category-discovery.sql.

WITH latest AS (
    SELECT DISTINCT ON (m.vv_media_id)
        m.id AS media_record_id,
        m.vv_media_id,
        m.version_no,
        m.media_payload::jsonb AS payload
    FROM core.media_record AS m
    JOIN common.lkup_media_dataset AS d ON d.id = m.dataset_id
    WHERE d.name = 'UK_GOV_NEWS_COMMUNICATIONS'
      AND m.vv_media_id IS NOT NULL
    ORDER BY m.vv_media_id, m.version_no DESC NULLS LAST, m.id DESC
), details AS (
    SELECT
        l.*,
        COALESCE(substring(pub.full_date FROM '^[0-9]{4}'), 'unknown') AS published_year,
        COALESCE(
            substring(src.url FROM '^https?://[^/]+/([^/?#]+/[^/?#]+)'),
            'other_or_missing_url'
        ) AS url_section,
        src.url AS source_url
    FROM latest AS l
    LEFT JOIN LATERAL (
        SELECT NULLIF(x.item ->> 'SourceURL', '') AS url
        FROM jsonb_array_elements(
            CASE jsonb_typeof(l.payload -> 'Sources')
                WHEN 'array' THEN l.payload -> 'Sources'
                WHEN 'object' THEN jsonb_build_array(l.payload -> 'Sources')
                ELSE '[]'::jsonb
            END
        ) WITH ORDINALITY AS x(item, position)
        WHERE NULLIF(x.item ->> 'SourceURL', '') IS NOT NULL
        ORDER BY x.position
        LIMIT 1
    ) AS src ON TRUE
    LEFT JOIN LATERAL (
        SELECT NULLIF(x.item ->> 'FullDate', '') AS full_date
        FROM jsonb_array_elements(
            CASE jsonb_typeof(l.payload -> 'Dates')
                WHEN 'array' THEN l.payload -> 'Dates'
                WHEN 'object' THEN jsonb_build_array(l.payload -> 'Dates')
                ELSE '[]'::jsonb
            END
        ) WITH ORDINALITY AS x(item, position)
        WHERE lower(x.item ->> 'Type') = 'published'
        ORDER BY (NULLIF(x.item ->> 'FullDate', '') IS NULL), x.position
        LIMIT 1
    ) AS pub ON TRUE
), ranked AS (
    SELECT
        d.*,
        row_number() OVER (
            PARTITION BY d.published_year, d.url_section
            ORDER BY md5(d.vv_media_id::text)
        ) AS section_rank
    FROM details AS d
), sample AS (
    SELECT * FROM ranked
    WHERE section_rank <= 12
    ORDER BY md5(vv_media_id::text)
    LIMIT 1500
)
SELECT
    media_record_id,
    vv_media_id,
    version_no,
    published_year,
    url_section,
    source_url,
    payload AS media_payload
FROM sample
ORDER BY published_year, url_section, md5(vv_media_id::text);
