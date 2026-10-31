"""Write an exact-ID UK metadata query and a preserved policy prompt; no API calls."""

import csv
import json
from pathlib import Path
import re

from contracts import POLICY_VERSION, file_digest


def main():
    root = Path(__file__).resolve().parent
    raw = root / "data/raw/adverse-news-uk-sample-5000.csv"
    csv.field_size_limit(100_000_000)
    with raw.open(encoding="utf-8-sig", newline="") as stream:
        identifiers = [row["media_record_id"] for row in csv.DictReader(stream, strict=True)]
    if len(identifiers) != 5000 or len(set(identifiers)) != 5000:
        raise ValueError("expected exactly 5000 unique physical article IDs")
    if any(not re.fullmatch(r"[0-9]+", value) for value in identifiers):
        raise ValueError("physical article IDs must be numeric")
    output = root / "data/requests/uk-5000-metadata.sql"
    output.parent.mkdir(parents=True, exist_ok=True)
    values = ",\n        ".join(f"({value}::bigint)" for value in sorted(identifiers, key=int))
    query = """-- Read-only. Exact physical IDs from the original UK CSV, not a new sample.
-- Keep missing records visible; do not restrict to is_current.
-- Export all 5000 result rows, with headers, as UTF-8 CSV:
-- research/adverseNews/data/raw/adverse-news-uk-metadata-5000.csv
WITH requested(media_record_id) AS (
    VALUES
        __IDENTIFIERS__
), selected AS (
    SELECT r.media_record_id AS requested_media_record_id,
           m.id, m.vv_media_id, m.version_no, m.content_hash,
           m.media_payload::jsonb AS payload,
           s.name AS source_name, d.name AS dataset_name
    FROM requested AS r
    LEFT JOIN core.media_record AS m ON m.id = r.media_record_id
    LEFT JOIN common.lkup_media_source AS s ON s.id = m.source_id
    LEFT JOIN common.lkup_media_dataset AS d ON d.id = m.dataset_id
)
SELECT
    m.requested_media_record_id AS media_record_id,
    m.id IS NOT NULL AS record_found,
    m.vv_media_id,
    m.version_no,
    m.content_hash AS database_content_hash,
    m.source_name,
    m.dataset_name,
    COALESCE(src.url, article_url.url) AS url,
    pub.full_date AS published_at,
    pub.original_value AS published_original,
    COALESCE(NULLIF(m.payload #>> '{Metadata,Language}', ''),
             NULLIF(m.payload #>> '{Taxonomy,Language}', '')) AS language
FROM selected AS m
LEFT JOIN LATERAL (
    SELECT NULLIF(x.item ->> 'SourceURL', '') AS url
    FROM jsonb_array_elements(
        CASE jsonb_typeof(m.payload -> 'Sources')
            WHEN 'array' THEN m.payload -> 'Sources'
            WHEN 'object' THEN jsonb_build_array(m.payload -> 'Sources')
            ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS x(item, position)
    WHERE NULLIF(x.item ->> 'SourceURL', '') IS NOT NULL
    ORDER BY x.position
    LIMIT 1
) AS src ON TRUE
LEFT JOIN LATERAL (
    SELECT NULLIF(x.item ->> 'URL', '') AS url
    FROM jsonb_array_elements(
        CASE jsonb_typeof(m.payload -> 'URLs')
            WHEN 'array' THEN m.payload -> 'URLs'
            WHEN 'object' THEN jsonb_build_array(m.payload -> 'URLs')
            ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS x(item, position)
    WHERE LOWER(x.item ->> 'URLType') = 'article'
      AND NULLIF(x.item ->> 'URL', '') IS NOT NULL
    ORDER BY x.position
    LIMIT 1
) AS article_url ON TRUE
LEFT JOIN LATERAL (
    SELECT NULLIF(x.item ->> 'FullDate', '') AS full_date,
           x.item ->> 'OriginalValue' AS original_value
    FROM jsonb_array_elements(
        CASE jsonb_typeof(m.payload -> 'Dates')
            WHEN 'array' THEN m.payload -> 'Dates'
            WHEN 'object' THEN jsonb_build_array(m.payload -> 'Dates')
            ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS x(item, position)
    WHERE LOWER(x.item ->> 'Type') = 'published'
    ORDER BY (NULLIF(x.item ->> 'FullDate', '') IS NULL), x.position
    LIMIT 1
) AS pub ON TRUE
ORDER BY m.requested_media_record_id;
""".replace("__IDENTIFIERS__", values)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(query)

    guide = root.parents[1] / "docs/PDG-115-adverse-news-labeling-guide.md"
    source = guide.read_text(encoding="utf-8-sig")
    match = re.search(r"### A\. System/developer message.*?```text\s*\n(.*?)\n```", source, re.S)
    if not match:
        raise ValueError("cannot locate accepted policy prompt")
    policy = root / "policy" / POLICY_VERSION
    policy.mkdir(parents=True, exist_ok=True)
    with (policy / "labeling-guide.md").open("xb") as stream:
        stream.write(guide.read_bytes())
    with (policy / "local-teacher-system.txt").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(match.group(1).strip() + "\n")
    snapshot = {
        "policy_version": POLICY_VERSION, "prompt_id": "pdg115-binary-labeler-v0.2",
        "source": "docs/PDG-115-adverse-news-labeling-guide.md, section 13A",
        "source_sha256": file_digest(guide),
        "files_sha256": {name: file_digest(policy / name) for name in
                         ("labeling-guide.md", "local-teacher-system.txt")},
        "status": "preserved policy, no model execution; pilot policy exceptions remain for review",
    }
    with (policy / "snapshot.json").open("x", encoding="utf-8") as stream:
        json.dump(snapshot, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"query": str(output), "physical_article_ids": len(identifiers),
                      "query_bytes": output.stat().st_size,
                      "policy_prompt": str(policy / "local-teacher-system.txt")}, indent=2))


if __name__ == "__main__":
    main()
