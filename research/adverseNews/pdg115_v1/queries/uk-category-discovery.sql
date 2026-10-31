-- PDG-115 UK_GOV category discovery. Read-only PostgreSQL.
-- Run each SELECT separately in the same database used for the earlier
-- uk-5000-metadata.sql export. No article body or full payload is exported.

-- A. How many physical versions and logical articles are in this dataset?
SELECT
    COUNT(*) AS physical_record_versions,
    COUNT(DISTINCT m.vv_media_id) AS logical_articles,
    MIN(m.id) AS oldest_physical_id,
    MAX(m.id) AS newest_physical_id
FROM core.media_record AS m
JOIN common.lkup_media_dataset AS d ON d.id = m.dataset_id
WHERE d.name = 'UK_GOV_NEWS_COMMUNICATIONS';

-- B. Show the shape of metadata on ten early and ten recent UK records.
-- This is deliberately small. It lets us write the final category-count
-- query using the fields actually saved in media_payload.
WITH sample_ids AS (
    SELECT id FROM (
        SELECT m.id
        FROM core.media_record AS m
        JOIN common.lkup_media_dataset AS d ON d.id = m.dataset_id
        WHERE d.name = 'UK_GOV_NEWS_COMMUNICATIONS'
        ORDER BY m.id ASC
        LIMIT 10
    ) AS early
    UNION
    SELECT id FROM (
        SELECT m.id
        FROM core.media_record AS m
        JOIN common.lkup_media_dataset AS d ON d.id = m.dataset_id
        WHERE d.name = 'UK_GOV_NEWS_COMMUNICATIONS'
        ORDER BY m.id DESC
        LIMIT 10
    ) AS recent
), sample AS (
    SELECT m.id, m.vv_media_id, m.version_no,
           m.media_payload::jsonb AS payload
    FROM core.media_record AS m
    JOIN sample_ids AS s ON s.id = m.id
)
SELECT
    s.id AS media_record_id,
    s.vv_media_id,
    s.version_no,
    s.payload #>> '{Content,Title}' AS title,
    (SELECT array_agg(k ORDER BY k)
     FROM jsonb_object_keys(s.payload) AS keys(k)) AS top_level_keys,
    (SELECT array_agg(k ORDER BY k)
     FROM jsonb_object_keys(
         CASE WHEN jsonb_typeof(s.payload -> 'Metadata') = 'object'
              THEN s.payload -> 'Metadata' ELSE '{}'::jsonb END
     ) AS keys(k)) AS metadata_keys,
    s.payload -> 'Metadata' AS metadata,
    s.payload -> 'Taxonomy' AS taxonomy,
    s.payload -> 'Sources' AS sources
FROM sample AS s
ORDER BY s.id;
