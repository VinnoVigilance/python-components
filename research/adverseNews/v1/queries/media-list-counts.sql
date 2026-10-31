-- PDG-115. Read-only PostgreSQL. Run in the same database as the UK query.
-- "list_name" here means media dataset name (for example PTV_NEWS).
-- saved_record_versions counts physical history rows; distinct_articles counts
-- each vv_media_id once, so an updated article does not inflate that count.

SELECT
    s.name AS source_name,
    d.name AS list_name,
    COUNT(*) AS saved_record_versions,
    COUNT(DISTINCT m.vv_media_id) AS distinct_articles
FROM core.media_record AS m
JOIN common.lkup_media_source AS s ON s.id = m.source_id
JOIN common.lkup_media_dataset AS d ON d.id = m.dataset_id
GROUP BY s.name, d.name
ORDER BY distinct_articles DESC, s.name, d.name;

-- Optional second query: UK_GOV metadata coverage among the latest saved
-- version of each distinct article. This checks whether the empty 20-row
-- sample reflects the full UK collection. No article text is returned.
WITH latest_uk AS (
    SELECT DISTINCT ON (m.vv_media_id)
           m.vv_media_id,
           m.media_payload::jsonb AS payload
    FROM core.media_record AS m
    JOIN common.lkup_media_dataset AS d ON d.id = m.dataset_id
    WHERE d.name = 'UK_GOV_NEWS_COMMUNICATIONS'
    ORDER BY m.vv_media_id, m.version_no DESC, m.id DESC
)
SELECT
    COUNT(*) AS distinct_articles,
    COUNT(*) FILTER (WHERE payload #> '{Metadata,Tags}' <> '[]'::jsonb
                     AND payload #> '{Metadata,Tags}' IS NOT NULL) AS with_tags,
    COUNT(*) FILTER (WHERE payload #> '{Metadata,Topics}' <> '[]'::jsonb
                     AND payload #> '{Metadata,Topics}' IS NOT NULL) AS with_topics,
    COUNT(*) FILTER (WHERE payload #> '{Metadata,Categories}' <> '[]'::jsonb
                     AND payload #> '{Metadata,Categories}' IS NOT NULL) AS with_categories,
    COUNT(*) FILTER (WHERE payload -> 'Taxonomy' IS NOT NULL
                     AND payload -> 'Taxonomy' <> 'null'::jsonb
                     AND payload -> 'Taxonomy' <> '{}'::jsonb) AS with_taxonomy
FROM latest_uk;
