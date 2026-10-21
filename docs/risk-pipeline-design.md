# PDG-95 risk classification pipeline

Implementation prepared on 2026-10-20. The media and watchlist paths share a
small processing loop and use separate classifiers and SQL repositories.
The schema and both packaged models passed a bounded local database smoke run on
2026-10-20. The automated suite passed on 2026-10-21; deployment integration
remains outstanding.

## Structure and ownership

```text
pipelines/riskCategoryPipeline.py
intelligence/risk/interface.py
intelligence/risk/mediaRiskClassifier.py
intelligence/risk/watchlistRiskClassifier.py
intelligence/risk/labelContract.py
intelligence/risk/mediaRiskCategory/
intelligence/risk/watchlistRiskCategory/
services/risk/mediaRiskService.py
services/risk/watchlistRiskService.py
services/risk/processing.py
services/risk/reviewService.py
jobs/riskCategoryJob.py
repositories/risk/mediaRiskRepository.py
repositories/risk/watchlistRiskRepository.py
repositories/risk/riskReviewRepository.py
repositories/risk/riskProgressRepository.py
scripts/sql/PDG-95-risk-assessments.sql
```

The interface constructs one of two classifiers. Intelligence receives canonical
payloads and calculates results; it does not query databases. Services select
work, load the model once, and manage transactions. Repositories contain explicit
SQL for each domain. There is no configurable table registry or plugin framework.
The media predictor and watchlist inference helpers do not import research code.

The business workbook is shared by both classifiers through labelContract.py.
Its stable label IDs and content fingerprint define taxonomyVersion. The former
media contract module retains compatibility imports. No Excel file was edited.

## Tables and source references

| Domain | Initial input | Incremental input | Results | Logical identity | Physical source/version |
| --- | --- | --- | --- | --- | --- |
| Watchlist | core.watchlist_member | delivery.watchlist_daily_delta_actions | core.member_risk_category | vv_member_id | watchlist_member_id, version_no |
| Media | core.media_record | delivery.media_daily_delta_actions | core.media_risk_category | vv_media_id | media_record_id, version_no |

Media reads media_payload; watchlists read full_payload. Scan only current core
rows. Current watchlist DELETED tombstones expire existing risk and are never
classified. The supplied media SQL is the naming reference. The PDG-95 migration
was applied to the configured local database on 2026-10-20; other environments
are unchanged.

The user confirms media normalization reprocessing feeds canonical changes into
media deltas. The supplied SQL defines tables, not delta-generation procedures.
Media delta production and scheduler configuration are external prerequisites;
this change does not install a scheduler or modify ingestion/delivery jobs.

## Initial and incremental

INITIAL scans current core records and never reads deltas. Run it for the first
load or after a model, rule, policy or taxonomy change. Repeating it skips
matching assessments; changed results create a new assessment even when the
source record itself has not changed. Risk REPROCESS is rejected because
INITIAL already provides that operation. Ingestion REPROCESS remains unchanged.

Risk retries use the existing jobStateRepository file mechanism used by the
adverse-media to-do list. No database checkpoint table or delta_effective_date
column is added. Per-domain files are data/state/risk/media.json and watchlist.json.
Human-readable diagnostics remain in the CLI job error logs.

The state contains next_date (normal scan frontier), inflight_date (interrupted
or row-limited date), and items (failed work keyed by mode and work ID). Each
pending entry stores mode, effective_date, action/scan ID, logical subject ID,
physical source-version ID for deltas, error type/message, accumulated attempts,
and INITIAL source scope. An exhausted record failure is saved immediately.
Successful retries remove only that work entry. Normal successful records do not
rewrite the file. The shared temporary-file/rename mechanism and per-domain
advisory lock protect updates. Preserve/share files across job hosts and reconcile
them after database resets.

On the next run, retry pending items first, ordered by effective date and work ID,
using their exact saved delta source versions. Each record gets up to three
attempts, with one database transaction per attempt. Failed attempts preserve
previous assessments and are logged with identifiers and traceback. The queue
entry remains after exhausted retries; it does not prevent new dates processing.
INITIAL entries use the normal current-core lookup on retry. A source-filtered
INITIAL invocation retries only its scope; incremental can retry all saved items.

After retries, scan only new dates from next_date through today or the optional
--effective-date target. Ordinary queued failures do not move next_date backwards.
Example: one October 2 record fails and October 3-20 complete. The next job retries
only that failed work item, then scans October 21; it does not rerun October 2-20.
Pending entries newer than an explicit target are retained for a later run.

With no state frontier, use the latest existing risk created_at date in host
local time and replay that date inclusively. This is a user-selected baseline,
not proof of historical batch coverage. Without assessments or saved scan state,
incremental returns INITIAL_REQUIRED; it never starts at the earliest delta date.
Missing delta dates count as empty. Late actions for already passed dates are not
automatically detected unless represented by a saved pending item.

Save inflight_date before scanning a date and advance next_date only after its
scan finishes and all exhausted failures have been persisted. An abrupt crash or
row limit can repeat that one date, but does not rewind later dates due to an
ordinary record failure. Existing legacy replay_from state is conservatively
rescanned once to recover unknown failed IDs, then converted to the new queue.
INITIAL has no scan frontier: after a crash, rerun INITIAL; its saved record
failures are queued and successful matching assessments skip insertion.

Incremental ADD/UPDATE loads the exact physical source row referenced by the
delta. Compare it with the current core row. When they differ, classify the older
row and insert its result with is_current=false and equal processing-time
valid_from/valid_to timestamps. Do not expire or replace the current assessment.
Thus fixing an October 2 historical version cannot displace October 20's current
assessment, and October 20 does not need to be reclassified for restoration.
Historical DELETE actions likewise cannot expire a newer current result.

Record identity and assessment hashes prevent duplicate inserts. Queue retry
success can be a new historical/current assessment or an identical skipped result.
An identical hash never promotes an archived assessment. Current source deletion
expires risk without inference. Missing exact source references remain queued;
keep referenced core history available.

Run statuses are SUCCESS, PARTIAL, FAILED, LIMIT_REACHED and INITIAL_REQUIRED.
Remaining pending items yield PARTIAL even when outside a deliberately selected
scope or date target. The CLI exits nonzero unless SUCCESS and reports pending_count
and retried_count. --max-rows includes retries and normal work. A limited scan
restarts its interrupted date; raise/remove the cap to finish a large prefix.

## Running

Use the repository's Python 3.11.9 vv-env from the repository root. Install both
requirements files first. The recommended combined job is:

```powershell
./vv-env/Scripts/python.exe -m jobs.riskCategoryJob --initial
./vv-env/Scripts/python.exe -m jobs.riskCategoryJob --incremental
```

Both commands run media first and watchlist second. Each domain retries only its
queued failed IDs, then performs its selected mode. INITIAL scans current core
rows; INCREMENTAL continues new delta dates through today. The classifiers,
per-domain advisory locks and file-backed queues remain separate. A domain error
does not prevent attempting the other domain; interruption stops the whole job.
No flag defaults to incremental. --mode INITIAL/INCREMENTAL is also accepted.

```powershell
./vv-env/Scripts/python.exe -m jobs.riskCategoryJob --incremental --subject media
./vv-env/Scripts/python.exe -m jobs.riskCategoryJob --initial --subject watchlist
./vv-env/Scripts/python.exe -m jobs.riskCategoryJob --incremental --effective-date 2026-10-20
./vv-env/Scripts/python.exe -m jobs.run
```

The menu now offers Risk categories and asks for INITIAL/INCREMENTAL. Running
`python jobs/riskCategoryJob.py --incremental` from an activated vv-env is also
supported. For an inactive environment use the explicit vv-env interpreter above.
--batch-size bounds database reads; --max-rows limits each domain separately,
including retries; --threads controls the watchlist CPU model. Omit row limits
for a full run. --effective-date is an inclusive incremental target only.

The combined job uses logs/risk/<run-id>.job.log, .errors.log, .full.log and
.events.jsonl. Log entries identify media or watchlist; state stays in
data/state/risk/media.json and watchlist.json. Its result contains domain
outcomes and log paths. Exit 0 means both selected domains succeeded; exit 2
means any domain failed, remains partial/pending, hit a row limit or needs INITIAL.
Keyboard interruption exits 130. The summary prints a reason for INITIAL_REQUIRED.

The job loads .env and uses WATCHLIST_JOB_TIMEZONE for log timestamps when set,
as the media job does. Processing dates use the execution host's local timezone;
configure it consistently with the business schedule. MEDIA_RISK_MODEL_DIR and
WATCHLIST_RISK_MODEL_DIR continue selecting the packaged models independently.
This job does not ingest sources, normalize raw files, generate delta actions,
install a scheduler or alter the database schema. Run upstream work separately.

The existing single-domain pipeline remains available for advanced source/model
selection:

```powershell
./vv-env/Scripts/python.exe -m pipelines.riskCategoryPipeline --subject media --initial --source SEC_PH_ADVISORIES
./vv-env/Scripts/python.exe -m pipelines.riskCategoryPipeline --subject watchlist --initial --source OFAC-SDN
./vv-env/Scripts/python.exe -m pipelines.riskCategoryPipeline --subject watchlist --initial --model-path intelligence/risk/watchlistRiskCategory/models/student-e5small-v6
```

--source means media dataset name or watchlist ListName and applies to INITIAL.
--initial-load and --list remain aliases. This older pipeline defaults to
watchlist/incremental and writes per-domain logs under logs/risk-<subject>.
Risk REPROCESS is rejected; ingestion REPROCESS is unchanged. --use-llm and the
old Ollama --model option are unsupported. Risk orchestration stays under
services/risk, and SQL stays under repositories/risk. Standalone media prediction
uses intelligence.risk.mediaRiskCategory.predictor.MediaRiskPredictor.

## Model packaging and replacement

The watchlist model package contains the original student_e5small_v6 weights,
tokenizer/config, manifest and runtime-only text/lookup/highlighter helpers.
The research originals remain intact. Asset checksums are verified on loading;
the runtime loads local files only and runs on CPU. Weights are covered by Git LFS.

The frozen v6 settings are 0.5 passage cutoff, any-passage aggregation, 0.5 victim
gate, 150-character neighboring context, 256-token paired inputs and 1,200-character
victim input. Lookup labels remain independent of the victim gate. Runtime
candidate selection uses the v6 fighting-conduct exclusions. These cutoffs were
preserved, not retuned. V6 still needs untouched independent evaluation.

The application requirements pin torch 2.14.1 and transformers 5.19.0 from
the research environment. Both and the media model's exact dependencies were
installed in `vv-env`. Real CPU inference succeeded for both packaged models;
`pip check` reported no broken requirements. Media retains the recorded
Python/scikit-learn/NumPy/SciPy/Joblib environment checks.

Dependency audit checked all direct imports in the new risk paths. Transformers
declares tokenizers and safetensors, which pip installs transitively; the saved
fast tokenizer uses tokenizer.json. Teacher/training libraries are not runtime
requirements. Official PyPI metadata confirms the selected torch/Transformers
releases and Windows Python 3.11 wheels for torch and scikit-learn. Local
dependency resolution and model execution were verified on 2026-10-20.

The previous Python 3.12 `vv-env` had incompatible package versions and missing
model dependencies. It was replaced with a Python 3.11.9 `vv-env`; both
requirements files install together and `pip check` passes. The user authorized
runtime verification before the usual commit-time test suite.

To replace a model:

1. Create a new version directory with the trained assets and a compatible
   manifest. Preserve the previous package and original training provenance.
2. Record the new identity, asset hashes, label order, environment and actual
   decision settings. Do not just rename a folder or edit a version string.
3. Select the directory with --model-path (or the domain model-directory
   environment variable) and run risk INITIAL over current records.
4. Resolve failures and rerun. Assessments for completed records retain their new
   model identity; failed records retain old assessments. Inspect versions before
   claiming complete rollout. The next incremental run must select the same model.

WATCHLIST_RISK_MODEL_DIR and MEDIA_RISK_MODEL_DIR select defaults for their domain.
There is no automatic retraining, registry server, model promotion or download.
Future watchlist packages may vary thresholds within the supported paired-passage
recipe. A different model architecture/recipe needs an explicit runtime change.

## Stored assessment and versioning

The stored payload uses one small shared shape:

```json
{
  "schemaVersion": "2.0",
  "assessmentStatus": "CLASSIFIED",
  "taxonomyVersion": "sha256:<taxonomy-fingerprint>",
  "riskCategories": [
    {
      "riskCategoryId": "SAN_GEN",
      "riskCategoryName": "Sanctions",
      "method": "lookup",
      "explanation": "",
      "metadata": {
        "rulesVersion": "sha256:<lookup-rules-fingerprint>",
        "calculationVersion": "sha256:<lookup-calculation-fingerprint>"
      }
    },
    {
      "riskCategoryId": "CR_FRAUD",
      "riskCategoryName": "Crime-Fraud",
      "method": "ml",
      "score": 0.82,
      "scoreType": "student_sigmoid",
      "threshold": 0.5,
      "evidence": [
        {
          "field": "Remarks",
          "text": "Illustrative passage describing fraud."
        }
      ],
      "explanation": "",
      "metadata": {
        "modelVersion": "watchlist-risk-e5small-v6:<weights-sha256>",
        "calculationVersion": "sha256:<text-calculation-fingerprint>",
        "victimThreshold": 0.5
      }
    }
  ],
  "reviewRequired": [
    {
      "riskCategoryId": "PEP_GEN",
      "riskCategoryName": "PEP",
      "method": "ml",
      "score": 0.76,
      "scoreType": "student_sigmoid",
      "threshold": 0.5,
      "evidence": [
        {
          "field": "Remarks",
          "text": "Illustrative passage describing public office."
        }
      ],
      "explanation": "",
      "metadata": {
        "modelVersion": "watchlist-risk-e5small-v6:<weights-sha256>",
        "calculationVersion": "sha256:<text-calculation-fingerprint>",
        "victimThreshold": 0.5
      }
    }
  ]
}
```

The example is illustrative, not a real inference result. Each emitted label
in riskCategories or reviewRequired contains its own metadata and an empty
explanation string. metadata identifies the model or lookup rules that generated
that label. explanation is reserved for a future human explanation; no explanation
is generated or written by the review service yet. Existing evidence remains
machine-selected source passages, separate from the future human explanation.

There is no separate labelMetadata map or metadata for unselected labels.
The shared taxonomyVersion identifies the business label catalogue. Schema 2.0
replaces the former record-level model metadata; existing schema-1.0 stored rows
are preserved. Review validation accepts either taxonomy location. Consumers must
support schema 2.0 before rollout. Current classifiers retain their existing models;
the payload permits a different model identity on each emitted label.

riskCategories contains final labels; reviewRequired contains labels awaiting
human review. Media selected labels are final. Watchlist lookup labels and ten
text labels are final; PEP, Corruption, Cybercrime, Tax Crime, and Organized Crime
remain pending when selected by the text model alone. Lookup takes precedence
when both methods support a label, so it is emitted only once.

The payload does not repeat the assessment row's source ID or `version_no`.
`assessmentStatus` is `CLASSIFIED` or, for media without usable body text,
`SKIPPED_EMPTY_BODY`. Failures are run outcomes, not stored assessments. The
watchlist victim check remains part of the frozen decision recipe; its cutoff is recorded in ML-label metadata. Full negative-label scores and
`supportedLabelIds` are not stored in the assessment JSON.

Each emitted label's metadata records the relevant model or rule identity and calculation
fingerprint (an identifier derived from exact assets, settings and runtime code).
The shared taxonomyVersion fingerprints the label catalogue. Lookup provenance
does not name the student model; ML provenance identifies its actual weights.
The hash covers the canonical assessment JSON, and skipping also requires the
assessment-table `version_no` to match the source row. A new source version
therefore receives a new assessment even if its calculated labels are unchanged.

| Version | Meaning |
| --- | --- |
| Table version_no | Source-content version being assessed |
| label.metadata.modelVersion | Named model/weights; media includes its training run ID |
| label.metadata.calculationVersion | SHA-256 fingerprint of relevant model assets, settings, and classifier/rule code |
| taxonomyVersion | Fingerprint of label IDs, names and descriptions |

Example: source row 101/version 1 has Fraud pending review under v6. An address
update creates row 205/version 2, still Fraud: expire the old assessment and
insert one pointing to 205/version 2. Reprocessing with v7 creates another
assessment pointing to 205/version 2, now with v7 provenance. Rerunning v7 with
unchanged inputs skips. Risk row ID distinguishes assessments; source `version_no`
does not increment because only the model changed. Historical assessments are
not rewritten.

CLASSIFIED with no labels is stored. SKIPPED_EMPTY_BODY is also explicit and
stored; it never means risk-free. FAILED calculation does not replace previous
labels with an empty result. Media evaluates 11 labels, watchlist 15 text labels
plus lookup-only scope; the workbook has 23 labels. Unsupported is unassessed.

## Persistent label review

The migration adds core.risk_label_review. Each event references exactly one
media or member assessment through a real foreign key and stores label ID,
present/absent/uncertain verdict, reviewer, time, taxonomy version, and optional
evidence/comment. Reviews can correct missed labels as well as reject predictions.
Machine output remains unchanged; repeated review events are retained.

```python
from services.risk.reviewService import record_review, list_reviews

record_review("media", assessment_id=123, label_id="CR_FRAUD",
              verdict="absent", reviewer="analyst-id", comment="Victim mentioned")
reviews = list_reviews(label_id="CR_FRAUD", verdict="absent", limit=100)
```

The taxonomy workbook must match the assessment fingerprint. For an older
assessment, supply taxonomy_path pointing to its preserved workbook snapshot.
Preserve those snapshots operationally before changing the business workbook.
The application does not edit or automatically create Excel workbooks.

Review queries return the original machine assessment and physical source ID,
allowing exact source-payload joins for training exports. They return events,
not an adjudicated gold dataset: repeated or disagreeing reviews need an explicit
accepted verdict. Unreviewed labels must not be converted to negative training
examples. There is no automatic training or reviewer UI in this change.

## Legacy code and deployment

The active risk CLI dispatches only to services/risk and intelligence/risk. The
unused Ollama risk classifier, old rule/report package, and PDF extraction script
were removed after repository-wide caller inspection. The generic PDF parser
remains registered in parsing/parserFactory.py and has callers and tests.

| Legacy item | Current use found | Follow-up |
| --- | --- | --- |
| Former `risk/` package, its report utility and `data/rules/riskClassification.xlsx` | No active pipeline caller | Removed together |
| Former `repositories/memberRiskCategoryRepository.py` | No in-repository imports found | Removed; replaced by `repositories/risk/watchlistRiskRepository.py` |
| Former risk wrappers under the media and watchlist service directories | No production imports found; old tests and README referenced them | Removed; active orchestration is in `services/risk` |
| `data/rules/VV_RiskCategory_Labels.xlsx` | Loaded by both active classifiers and review validation | Required active taxonomy; retain |
| `research/watchlistRiskClassification/lookups/watchlistRiskLookup.xlsx` | Research deliverable generated from `scripts/lookupRules.py`; no runtime import | Research artifact, not a production rules source |

The active watchlist lookup and highlighter rules are in
`intelligence/risk/watchlistRiskCategory/lookupRules.py` and `highlighter.py`.
They match the research helper code and are loaded directly by the classifier.
Python is the executable source of truth for frozen v6. The research workbook
`research/watchlistRiskClassification/lookups/watchlistRiskLookup.xlsx` is a
human-readable research copy, not runtime configuration. Do not maintain a
second production copy in Excel or YAML. If rule maintenance later moves to
non-engineers, YAML is the better candidate for declarative lookup data; keep
matching, text splitting, exclusions and candidate selection in Python, with
validation and one source of truth.
No active runtime rule file named `riskCategory.txt` or `risk_category.txt` was
found. The removed `data/prompts/risk_classification.txt` belonged only to the
retired LLM classifier; it was a prompt, not a lookup-rule source.

The SQL file was applied to the configured local database on 2026-10-20. It preserves the
supplied media/delta schema, adds current-row uniqueness and date/ID indexes,
and adds review storage. Existing tables are not silently rewritten by
CREATE TABLE IF NOT EXISTS. Duplicate current rows must be resolved deliberately
before unique indexes can be installed. The supplied ON DELETE CASCADE policy
means physical source deletion can remove unreviewed risk history; retain core
source history rather than physically deleting records. Reviews retain their
foreign-key references and block deletion of referenced assessments.

The local smoke run created four media and three watchlist assessments. Both
models loaded and inferred from canonical PostgreSQL payloads. The first three
media samples had no selected labels; article 113 selected `CR_FORGE` and
`REG_GEN` into `riskCategories`. All three sampled UN-SANCTIONS members selected
rule-backed `SAN_GEN`; one also selected `PEP_GEN` into `reviewRequired`.
Repeating bounded full scans skipped identical assessments. Source
version and single-current-row checks passed for all seven assessments. A
review insert passed the database constraints and was rolled back.

Both INCREMENTAL runs for 2026-10-20 returned SUCCESS with zero records because
their delta tables had no actions. This verifies the empty path only; ADD,
UPDATE, DELETE, supersession and failure recovery remain untested against real
delta records. The sample's `LIMIT_REACHED` statuses are expected because the
runs were capped; they do not indicate full data coverage. No full-corpus run or
accuracy evaluation was performed.

Read-only local checks on 2026-10-21 found zero rows in both delta-action
tables and zero completed dates in `core.spoke_run_log`. The watchlist delta
procedure exists in PostgreSQL; no media delta-generation procedure was found.
The risk tables held only the four media and three watchlist smoke assessments,
whose `created_at` dates cannot establish a delta checkpoint.

Before deployment, verify actual delta generation and scheduler wiring, then
exercise dated ADD/UPDATE/DELETE batches and retry behavior. No scheduler was
activated here. Before any commit, follow docs/agent/tests-and-deps.md: update
affected tests and run the suite. The old rule-engine/service tests were retired;
30 updated media contract tests and four processing tests passed. With the
rebuilt Python 3.11 `vv-env`, 37 focused risk tests passed and both packaged
models loaded. An earlier full-suite attempt was stopped; the final 2026-10-21
commit-time result below supersedes that incomplete run. The existing migration
must still be reviewed and applied in each target environment.


### Catch-up implementation status — latest revision

Failed-record queue recovery replaces the prior date-range replay implementation.
Only failed work IDs are retried; later successfully processed dates are untouched.
Database checkpoint and assessment-date-column proposals remain removed; local
read-only inspection confirmed they do not exist. Final verification performed no
live database writes or full loads. Research decision logic/assets remain unchanged.

Commit-time validation on 2026-10-21 used Python 3.11.9 vv-env:

- Focused risk/model-contract/combined-job tests: 66 passed.
- Full pytest suite: 959 passed, 35 skipped (994 collected, 157.00 seconds).
- The 35 database integration tests skipped because TEST_DATABASE_URL is unset.
- CLI help and git diff --check passed. No dependency or Excel files changed.

Recovery tests now cover exact-ID retries without replaying later successful
records, persistent failure counts, interrupted/limited dates, retry row budgets,
future pending items, queue write failures, isolated domain queues, empty-day
progress, INITIAL_REQUIRED, historical/current preservation and duplicate skipping.
Job tests verify both mode flags, domain selection, error isolation, logging,
incomplete status, interruption and invalid initial date arguments. Victim-gate
and label-routing tests preserve the existing research decision behavior.

Tests use temporary state/log files and mocked database/model execution. Live
PostgreSQL retry/crash behavior, concurrent ingestion, full-corpus inference and
media delta generation remain unverified. File state must remain persistent and
shared across hosts, and late deltas for already scanned dates need manual replay.

Schema 2.0 keeps metadata and an empty explanation inside emitted labels. A complete
illustrative JSON is in examples/risk-assessment-v2.watchlist.json. Only selected
labels retain model provenance; empty outputs can hash identically across models.


### Combined risk job

jobs/riskCategoryJob.py provides both domains under one INITIAL/INCREMENTAL
command and is exposed through jobs/run.py. It reuses JobLog and the existing
risk dispatcher, preserves per-domain locks/queues, and continues the second
domain after first-domain failure. The combined job is covered by the
commit-time tests above. No live classification/full load or database writes were
run for this addition; deployment checks remain outstanding.


### Server delivery compatibility — 2026-10-21

Server-supplied uncommitted changes split the delta wrappers into
watchlistDailyDeltaActionsGeneration.py and mediaDailyDeltaActionsGeneration.py,
and extend postIngestionSpokesDeltas.py to generate both domains. Keep this split
and the server's Docker Compose mount edits. Callers import the watchlist generator
directly from watchlistDailyDeltaActionsGeneration; the old module is removed.

Post-ingestion processing now attempts media even when watchlists are empty,
already caught up, or fail spoke synchronization. It reports domain failures
after attempting both domains. Incomplete spokes are an error and prevent search
view refresh. The existing watchlist backfill and added media backfill remain
available; no rebuild/backfill is executed by compatibility inspection.

Delta and spoke execution wrappers and search-view refresh restore a pooled
connection's previous autocommit setting before returning it, preventing delivery
execution settings from leaking into risk/ingestion callers. Procedure names and
generated spoke SQL behavior are unchanged. The media wrapper calls
delivery.generate_media_daily_delta_actions; its server deployment and actual
execution are not established by the presence of this Python wrapper.

Commit-time verification on 2026-10-21 passed syntax, direct imports and diff
checks. Focused delivery/risk/job tests: 52 passed, including 16 new delivery
regressions. Full suite: 975 passed, 35 skipped in 170.40 seconds (1,010 collected).
Database integration skipped because TEST_DATABASE_URL is unset. Delivery tests
mock connection/procedure execution and cover independent media work, propagated
watchlist errors, incomplete-spoke refresh prevention, correct procedure/date
calls and autocommit restoration on success/failure.

No live procedures were called/deployed, no view refreshed, and no live DB writes
or full loads were run. Actual server procedure behavior remains unverified.
Existing MAX(effective_date) watermarks retain limitations for empty days, same-day
arrivals and a delta created before a later spoke-sync failure; they are not proof
of a fully completed delivery date. These upstream rules were not redesigned here.
