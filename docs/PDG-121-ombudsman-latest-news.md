# PDG-121: Office of the Ombudsman latest news

- Dataset: `OMBUDSMAN_PH_LATEST_NEWS`
- Source: `OMBUDSMAN_PH`
- Category: Media; country: Philippines
- Public archive: https://www.ombudsman.gov.ph/?cat=64
- Branch: `feat/PDG-121-ombudsman-latest-news`, based on `origin/staging` at `2262b49`.

The existing API collector uses the WordPress query route `/wp/v2/posts` with category 64, sorted by publication date descending. It fetches up to 100 posts per request using offset pagination and stops after an empty response. No separate request is needed for an article's image or PDF links: they are extracted from the post's returned HTML. Attachment URLs are mapped as `Image` or `Document`; the pipeline does not download the files.

Discovery uses `full_scan`, so each run revisits every current post and can detect edits to older articles. This costs more requests as the archive grows than stopping at previously seen records. The order is newest publication date first, not modification date, so stopping early would miss changes to older posts. An article added or removed while offset pages are being fetched can shift page boundaries; a later full scan can recover missed records.

On 2026-11-01 the API reported 100 records; their IDs and order matched all ten archive pages. The local pipeline run completed with 100 raw and 100 core records and zero failures. Some content requires summary review, including an image-only article; linked images and PDFs are not converted to text. The coverage check reflects that date and does not prove all historical posts remain published.

The dataset and source were registered in the local `common.lkup_media_dataset` and `common.lkup_media_source` tables. Those database rows are local runtime state and are not included in this commit. The local SeaweedFS instance uses a separate local data directory and must be running for future pipeline runs.
