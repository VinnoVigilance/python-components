# Media Post Processing Design

Media post-processing starts after ingestion commits to `core.media_record`. It generates summaries and runs the adverse-news prediction model once in the post-processing flow. It saves the article analysis in `core.media_record_post_process`. A later consumer can combine current core article data with the active analysis.

## File placement

The following paths show the implementation responsibilities. The pipeline uses the existing server tables.

| File or directory | Responsibility |
| --- | --- |
| `pipelines/mediaPostProcessingPipeline.py` | Callable entry point. Validate the run mode, select and page through work using the repository, pass bounded batches to the service, and return run totals. |
| `services/adverseMediaPostProcessPipeline/mediaPostProcessingService.py` | Translate canonical article records into processor inputs, call both processors, build the payload and manifest, and apply the hash comparison and write policy. Own the transaction for each article. |
| `repositories/mediaPostProcess/mediaRecordPostProcessRepository.py` | Execute SQL for core and delta reads, source lookups and locks, and result lookup, expiry and insertion. Use the service's transaction for writes. |
| `intelligence/summary/mediaSummarizer.py` | Existing summarizer implementation. It selects sentences from article text and does not depend on normalization or the database. |
| `intelligence/adverseStatus/predictor.py` | Load and verify the frozen adverse-news model and expose batched prediction. |
| `intelligence/adverseStatus/models/adverse-news-word-char-lsa200-rbf-svm-v1/` | Versioned model bundle. Keep training, evaluation and offline CLI code on the research branch. |

Call the pipeline from Python for the initial implementation. The pipeline owns the predictor for the duration of the call and gives the service access to it for scoring. The downstream table described below consumes the saved result.

From the repository root, with the intended database configured, call the pipeline using the project Python environment:

```powershell
python -c "from pipelines.mediaPostProcessingPipeline import run_media_post_processing; print(run_media_post_processing('initial'))"
```

Use `incremental` after delta generation completes, or `reprocess` to recalculate the current core population. The defaults are 100 database rows per page and one million title and body characters per processing batch. A single larger article is processed alone and keeps its full text. The model loads on the first batch of current candidates. The returned counts and timings cover the scan, model load, combined preparation, writes, and overall scoring throughput. Calculation or database exceptions stop the call. Previously committed results remain available and a rerun compares their hashes before writing again.

The pipeline decides **which records to process**. The service decides **what a record produces and whether it changes the stored result**. The repository executes the required database operations. Calculate summaries and predictions outside the write transaction. Inside it, lock and recheck the source and active result, then compare and write. This avoids holding database locks during model inference and covers an article version changing between selection and write.

## Summary migration

Post-processing generates its own summary from committed `Content.Title` and `Content.BodyText`, even when `Content.Summary` exists. Existing core summaries can be older generated values, and the payload does not establish whether they came from the source. Keep the existing summarizer and parameters initially (`engine="hybrid"`, `sentences=0`, which chooses length adaptively). Store the text for `AUTO_CANDIDATE`; store null text plus the existing status for `MISSING_BODY` and `MANUAL_REVIEW`. A processor exception fails the calculation and must preserve the previous result.

Post-processing calls the existing summarizer after ingestion. The normalization handler no longer dispatches `GENERATE_MEDIA_SUMMARY`. Remove the retired rule from `data/rules/mediaPostNormalization.xlsx` when applying the workbook change. Source-provided summary mappings and the existing ingestion hash algorithm are preserved.

## Adverse news prediction

The local `research` branch at `6f97dba0` contains `research/adverseNews/v1/code/score_articles.py` and the frozen bundle. Reuse its full-text inference: title and body feed word and character features, then a 200-dimension text representation and a support vector machine. The score is a signed decision margin, not a probability. The research threshold is zero. Record the source name in the processor manifest when available. Source identity never gates scoring. The predictor uses the title and body. Use the original body for prediction independently of the summary. Preserve the research input requirement for nonblank title and body, and emit an explicit skipped status when either is missing.

The research release is a review-assisted pilot. Every article requires review, including negative scores and articles skipped for missing text. On the completed 35-article UK_GOV comparison the model found four of eight GPT-labeled positives, and the separate 400-article review was unfinished in this snapshot. Represent the positive or negative label as a **model prediction**, not a final adverse determination. The existing risk category classifier serves a separate classification task.

The research model is a Git LFS object of 132,527,744 bytes. The local object matches SHA-256 `eb5829ac409f02d79bd161d973574f82141eaf7bde512da2164987c487df05da`. Package the real artifact using Git LFS and verify its checksum before loading. The repository and research requirements specify matching NumPy, SciPy, scikit-learn and joblib versions. Compare runtime outputs with the research scorer for the same complete articles, including score and threshold decision.

Each pipeline run owns one predictor. Verify and load the versioned model once when there are eligible articles to score, reuse it for all batches, and release model and batch references when the run exits, including on failure. Keep the predictor out of module-level caches and returned run results. In a persistent Python session, releasing the model makes its memory available for reuse, but the Python or native allocator may retain some memory for the process. If operation requires all process memory returned to the OS at run end, call the same pipeline from a short-lived Python process.

Bound inference batches by article count and total text volume while preserving full article text. The research scorer batches in groups of 100, which gives a comparison point. Start with one process. Verify and load the model before the first result write for a run that needs prediction so a load failure cannot leave partially updated results.

Return a compact performance summary with run totals. Measure elapsed time for model verification and load, database reads, summarization, model inference, and database writes. Track rows scanned, articles scored, rows written, and scored articles per second. During profiling, sample process RAM before model load, after load, during representative batches, and after cleanup. Use a process-wide memory measure so model memory allocated by native libraries is included. A sampled maximum is an estimate, and the artifact size on disk is separate from working memory. Compare batch sizes on the same representative articles using throughput and memory together before choosing a default. Keep normal logging at batch and run level.

## Proposed article output and downstream consumer

An example `analysis_payload` for a UK_GOV article is:

```json
{
  "summary": {"status": "AUTO_CANDIDATE", "text": "The regulator announced a new enforcement action."},
  "adversePrediction": {
    "status": "SCORED",
    "modelPositive": true
  }
}
```

Hash the summary and decision fields, with execution timestamps excluded. Store the model version, artifact checksum, threshold, score, review requirement, source name, summarizer version, settings and flags in `processor_manifest`. The score is outside the hash: a score-only change does not create a new row, so its manifest continues to describe the last saved payload. A skipped prediction has a status that distinguishes missing input from a scored negative.

The implemented `MediaPostProcessingService.prepare_batch` accepts core rows containing `id`, `vv_media_id`, `version_no`, `media_payload` and the resolved `source_name`; it returns their prepared table fields in the same order. It uses the predictor instance supplied by its caller, discards large summarizer intermediates per article, and performs no database writes. A calculation error propagates for the batch; the future caller must report failures and must not write an incomplete result. The repository resolves names using `common.lkup_media_source`, consistent with configured source names such as `UK_GOV` and `DOJ_PH`; verify deployed lookup values and routing before a run. Run-level batch and memory limits remain integration work.

`save_result` checks the prepared hash and opens one transaction per article. It locks the exact current core row before inspecting the active result. This uses the same core-row lock as ingestion and serializes post-processing writers even when no result exists yet. It returns `STALE` if the source version is no longer current, `UNCHANGED` for a matching hash, or `INSERTED`/`UPDATED` after a write. PostgreSQL's transaction timestamp supplies the same time to expiry and insertion; an error rolls both back. The pipeline counts stale sources and logs unexpected actions and invalid references. DELETE actions bypass the processors and expire the active result when it is not newer than the referenced source version.

The downstream table should join the current core article to the active analysis by `vv_media_id` and preserve both source version references where useful. The target table name, schema and refresh mechanism still need definition. Its inputs can change independently. For example, if only an article's publisher metadata changes, the core version can advance while an identical analysis remains linked to the previous core row. A downstream table that refreshes only when post-processing inserts a row would miss that core change. Give the downstream refresh access to changes on both inputs, or rebuild the relevant projection from both when it runs.

## Run modes

| Mode | Input | Behavior |
| --- | --- | --- |
| Initial | Current, non-deleted articles in `core.media_record`, in bounded batches | First population of an empty destination. Complete the full initial load before running incremental. |
| Reprocess | The same current, non-deleted core article population as initial | Recalculate with the deployed processors. Insert missing results, skip matching hashes, and version changed results. New inserts can move the next incremental start date. |
| Incremental | `delivery.media_daily_delta_actions` from the latest destination insertion date through the current database date, inclusive | Scan in bounded batches. For an ADD or UPDATE, use the exact core version referenced by the delta and apply the shared result write rules if that version is still current. |

A failed or capped initial run is incomplete. A retry is recovery of the original work, not a fourth mode.

## Delta contract

VVIF Data Insertion Guideline v1.6, PDF pages 41–50, specifies **watchlist** daily deltas. It selects core versions inserted on the effective date, groups by stable member ID, and emits the final synchronization action and exact core row for that member that day. It neither compares full snapshots nor emits every intermediate version. It can generate a historical date, regenerate a day transactionally, or complete a day with zero changes. Pages 67–80 describe media ingestion and do not specify an equivalent media generator contract.

Media ingestion inserts new article versions and closes replaced versions. The checked application code contains no media removal path. The media delta schema permits ADD, UPDATE and DELETE. The application calls the deployed generator, so review its definition and observed action counts before drawing conclusions about what it emits. Media post-processing and media risk expire the active result for a valid DELETE delta and retain its history.

The shared window policy in services/common/incrementalDeltaScan.py gets the latest insertion date from a module's destination repository and the current date from PostgreSQL. Each repository owns its SQL and table names. The incremental service yields each calendar day in the inclusive window in order. Both pipelines finish paging one effective date before moving to the next. For example, a November 13 start and a November 15 end scan November 13, 14 and 15 in order. Media risk uses the same start rule on regular runs and preserves explicit replay and interrupted-date recovery. An empty destination requires an initial load unless media risk has explicit recovery work.

Coordinate with delta generation because pagination is not a snapshot of a regenerating table.

## Result identity and writes

Use `vv_media_id` to find the current destination result. Use `media_record_id` to read the exact source row and retain it, along with `media_version`, as provenance for the result that was saved. The ingestion guideline's risk example on pages 63–65 looks up the active result by stable ID and treats an equal result hash as a no-op. The actual media risk job also looks up by stable ID, but its no-op additionally requires the exact same source row and version. Post-processing deliberately follows the requested payload-only comparison.

Hash the canonical `analysis_payload` with the existing `utils.hashing.calculate_record_hash` and store it in `post_process_hash`. Compare against the active row only:

| Existing active result | Action |
| --- | --- |
| None | Insert the calculated payload, hash, processor manifest, article ID and source version. |
| Matching hash | No insert and no update. The saved source version and manifest still describe the last changed result. |
| Different hash | Set the previous row's `to_date` and insert the new row with the same timestamp as `from_date`, null `to_date`, and its own processor manifest. Do both in one transaction. |

The supplied DDL derives `is_current` from `to_date IS NULL`; do not write the flag directly. Keep the old payload, hash, manifest, source identity, and creation timestamp unchanged. The supplied SQL is design reference material. Its index is on `media_record_id`. Verify the deployed index and check for duplicate current rows by `vv_media_id` before the first run. The application uses the existing server table. Serialize writes by logical article and recheck source and result state inside the transaction, including the case where no destination row exists yet.

Example: article 410 moves from core row 9001 to 9008. If the analysis is unchanged, keep the active result linked to 9001. Read today's analysis by stable article ID; do not require the saved source row to equal today's core row. If analysis changes, close the old result and insert the new one linked to 9008. A processor version change alone does not create a result row when the payload is identical.

ADD and UPDATE run both processors for the current source version and compare the payload hash before writing. DELETE expires the active result by stable article ID, setting its to_date so the generated is_current becomes false. A repeated DELETE with no active result leaves history unchanged. A DELETE for an older version cannot expire a newer active source or result.

## Before the first run

On November 16, 2026, the three post-processing modes passed a local PostgreSQL run in a disposable database with synthetic core and delta rows, the supplied article table design, and the actual summarizer and adverse-news model. Initial scored and inserted two articles. Reprocess scored both again and wrote no rows because their hashes matched. Incremental selected November 14 through November 16 with one delta per date and IDs decreasing across the dates. With a page size of one, it processed all three actions, scoring two articles, inserting one result, replacing one result with a different hash, and expiring one result. The final checks found two current rows and two expired historical rows. A final reprocess made no further writes. The disposable database was removed after validation.

Additional post-processing write checks confirmed that the same payload hash across core versions leaves the current result unchanged, stale saves and deletes are rejected, and a deliberately failed replacement insert rolls back the preceding expiry. A successful replacement sets the old end and new start to the same timestamp. Repeating a valid DELETE makes no further changes.

Media risk passed a separate local PostgreSQL run with its real classifier and synthetic rows. Initial inserted two assessments. Incremental scanned November 13, 14, 15, and 16 in order, even though delta IDs decreased across dates. It processed UPDATE, an empty day, DELETE, and ADD respectively. Replaying November 16 inserted no duplicate. The final history contained two current and two expired assessments. Both disposable databases used for these additional checks were removed.

The configured local `vinno_vigilance` database contained 19,057 core media rows, 18,867 marked current, and an empty delta table. The supplied SQL created both post-processing tables locally. A full initial run stopped with PostgreSQL reporting `out of memory for query result`; no results were written. A later read-only check successfully fetched the first page at sizes 1, 10, and 100, so the cause was not isolated.

An isolated local database used 12 existing core articles and their source mappings with the supplied destination SQL. Initial scored and inserted the 11 articles marked current for that run. Reprocess rescored all 11 and left their results unchanged. For incremental, three controlled deltas covered November 14–16. UPDATE pointed to a new version with missing body text and replaced the prior scored result, DELETE expired a result, and ADD inserted a result. The final table had 13 rows, 11 current, with one current result per stable article ID. The isolated database was removed. This verified the consumer against existing article data and controlled deltas. Full-volume local processing and the intended server's deployed delta generator and destination schema still require verification.

1. On the intended database, inspect the deployed generator definition read-only:

   ```sql
   SELECT p.oid::regprocedure AS routine, pg_get_functiondef(p.oid) AS definition
   FROM pg_proc p
   JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname = 'delivery'
     AND p.proname = 'generate_media_daily_delta_actions';
   ```

   Check the business-day timezone and date filter, grouping key, latest-version selection, ADD/UPDATE/DELETE mapping, same-day regeneration behavior, and where generation completion is recorded. Inspect two completed dates and trace representative delta pointers to core. Distinguish a confirmed empty day from a missing day using upstream run status.

2. Inspect the installed post-processing table and indexes. Verify one article through both processors, confirm whether the downstream table requires the model margin, and confirm the table name and field mapping. Member output needs a separate stable extracted-member identity and is pending its contract.
3. On a non-production database, record counts and outcomes for initial, same-input reprocess, changed-output reprocess, and an incremental scan over multiple dates in the inclusive window. Verify that replay causes no duplicate current rows, superseded inputs cannot replace current results, DELETE expires only the matching active result and keeps its history, and a failed write leaves the previous result intact. Record commands and results in the runbook once the implementation and target are available.
