# PDG-95 risk classification pipeline

Implementation prepared on 2026-10-20. The media and watchlist paths share a
small processing loop and use separate classifiers and SQL repositories.
The schema and both packaged models passed a bounded local database smoke run on
2026-10-20. The full test suite and deployment integration remain outstanding.

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
repositories/risk/mediaRiskRepository.py
repositories/risk/watchlistRiskRepository.py
repositories/risk/riskReviewRepository.py
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

| Domain | Initial/reprocess input | Incremental input | Results | Logical identity | Physical source/version |
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

## Initial, incremental and reprocess

INITIAL populates assessments from current core records and never reads deltas.
REPROCESS deliberately reassesses those current records after a model, rule,
policy or taxonomy change. They share the same bounded scanner and persistence
rules. Repeating initial is safe, but reprocess expresses the operational intent.

INCREMENTAL consumes ADD/UPDATE/DELETE from one required effective_date. Pass
the date of the delta batch actually generated. Automatically choosing the latest
date could skip an older failed batch. Process missed dates explicitly. The
scanner fixes an upper ID boundary per run.

Incremental references are compared with the exact current physical source row.
A superseded delta is counted and cannot overwrite a newer source assessment.
DELETE expires the logical subject's current assessment unless its source has
already been replaced with a newer non-deleted record. Historical backfills do
not reconstruct old assessments for source rows that are no longer current.

Each record commits its old-result expiry and replacement together. Calculation
failure rolls back that record, keeps the old assessment, logs the work identity,
and increments failed_count. Rerunning the same selection retries failed work;
completed identical results skip. Classifier initialization failures abort once.
Deletion-only work does not require loading model dependencies.

There is one advisory job lock per domain, shared across all three modes. The
migration adds partial unique indexes for one current assessment per logical ID.
Run status is SUCCESS, PARTIAL, FAILED or LIMIT_REACHED. These are implementation
statuses, not statuses defined by the insertion guideline. The CLI exits nonzero
for any status other than SUCCESS. A partial run logs and counts failed records,
preserves each failed record's previous assessment, and continues with later
records. The operator can retry by rerunning the same selected work. A bounded
run reports `limit_reached` only when work remains, including when some records
failed. A scheduler must not advance to a newer delta date after a partial or
limited run without retrying the same effective date; the pipeline does not keep
a durable queue of failed dates. After a `--max-rows` run, remove that cap (or
raise it beyond the already scanned prefix) for the retry; the scan restarts
from the beginning and skips matching completed assessments.

## Running

Use the repository's Python 3.11.9 `.venv` from the repository root. Both models
ran with its pinned dependencies. The older `vv-env` uses Python 3.12.7 and does
not match the packaged media model's environment.

```powershell
./.venv/Scripts/python.exe -m pipelines.riskCategoryPipeline --subject media --mode initial
./.venv/Scripts/python.exe -m pipelines.riskCategoryPipeline --subject media --mode incremental --effective-date 2026-10-20
./.venv/Scripts/python.exe -m pipelines.riskCategoryPipeline --subject media --mode reprocess --source SEC_PH_ADVISORIES
./.venv/Scripts/python.exe -m pipelines.riskCategoryPipeline --subject watchlist --mode initial --source OFAC-SDN
./.venv/Scripts/python.exe -m pipelines.riskCategoryPipeline --subject watchlist --mode reprocess --model-path intelligence/risk/watchlistRiskCategory/models/student-e5small-v6
```

--source means media dataset name or watchlist ListName; it applies only to
initial/reprocess. --batch-size bounds reads; --max-rows deliberately limits a
run. --initial-load and --list remain CLI aliases. The default subject and mode
are watchlist/incremental, but that invocation requires --effective-date; the
former hidden initial-load/Ollama block has been removed. --use-llm and the
Ollama --model argument are no longer supported. Risk orchestration lives only
under services/risk. Direct media prediction uses
intelligence.risk.mediaRiskCategory.predictor.MediaRiskPredictor.

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
installed in `.venv`. Real CPU inference succeeded for both packaged models;
`pip check` reported no broken requirements. Media retains the recorded
Python/scikit-learn/NumPy/SciPy/Joblib environment checks.

Dependency audit checked all direct imports in the new risk paths. Transformers
declares tokenizers and safetensors, which pip installs transitively; the saved
fast tokenizer uses tokenizer.json. Teacher/training libraries are not runtime
requirements. Official PyPI metadata confirms the selected torch/Transformers
releases and Windows Python 3.11 wheels for torch and scikit-learn. Local
dependency resolution and model execution were verified on 2026-10-20.

Local vv-env also has NumPy 2.5.1 and SciPy 1.18.0, differing from the media pins,
and lacks scikit-learn, Joblib, torch and Transformers. Installing into a separate
Python 3.11 `.venv` preserved that environment and supplied the model-required
pins. The user authorized this runtime verification before the usual commit-time
test suite.

To replace a model:

1. Create a new version directory with the trained assets and a compatible
   manifest. Preserve the previous package and original training provenance.
2. Record the new identity, asset hashes, label order, environment and actual
   decision settings. Do not just rename a folder or edit a version string.
3. Select the directory with --model-path (or the domain model-directory
   environment variable) and run risk REPROCESS over current records.
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
  "schemaVersion": "1.0",
  "assessmentStatus": "CLASSIFIED",
  "riskCategories": [
    {"riskCategoryId": "LE_GEN", "riskCategoryName": "Law Enforcement"}
  ],
  "reviewRequired": [
    {
      "riskCategoryId": "CR_FRAUD",
      "riskCategoryName": "Crime-Fraud",
      "method": "ml",
      "score": 0.82,
      "scoreType": "student_sigmoid",
      "threshold": 0.5,
      "evidence": [
        {"field": "Remarks", "text": "Illustrative passage"}
      ]
    }
  ],
  "metadata": {
    "modelVersion": "watchlist-risk-e5small-v6",
    "taxonomyVersion": "sha256:<taxonomy fingerprint>",
    "calculationVersion": "sha256:<model assets, settings, and runtime fingerprint>"
  }
}
```

The example is illustrative. `riskCategories` contains labels eligible for
client display. `reviewRequired` contains selected text-model labels awaiting
human review. Media selected labels go directly to `riskCategories` with score
and cutoff, and media `reviewRequired` is empty. Watchlist lookup labels
go directly to `riskCategories`. If a lookup already supports a label, the same
label is not duplicated in `reviewRequired`. The human-review event is stored
separately and does not yet promote labels into the client-facing array.

The payload does not repeat the assessment row's source ID or `version_no`.
`assessmentStatus` is `CLASSIFIED` or, for media without usable body text,
`SKIPPED_EMPTY_BODY`. Failures are run outcomes, not stored assessments. The
watchlist victim check remains part of the frozen decision recipe, but its
diagnostic is not serialized. Full negative-label diagnostics and
`supportedLabelIds` are not stored in the assessment JSON.

Metadata records a readable model name/version, a label-catalogue fingerprint,
and one calculation fingerprint covering model assets, decision settings,
and relevant runtime code (including watchlist lookup rules).
The hash covers the canonical assessment JSON, and skipping also requires the
assessment-table `version_no` to match the source row. A new source version
therefore receives a new assessment even if its calculated labels are unchanged.

| Version | Meaning |
| --- | --- |
| Table version_no | Source-content version being assessed |
| modelVersion | Named training run/model; media includes its training run ID |
| calculationVersion | One SHA-256 fingerprint of exact model assets, decision settings, and classifier/rule code |
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
Repeating INITIAL and bounded REPROCESS skipped identical assessments. Source
version and single-current-row checks passed for all seven assessments. A
review insert passed the database constraints and was rolled back.

Both INCREMENTAL runs for 2026-10-20 returned SUCCESS with zero records because
their delta tables had no actions. This verifies the empty path only; ADD,
UPDATE, DELETE, supersession and failure recovery remain untested against real
delta records. The sample's `LIMIT_REACHED` statuses are expected because the
runs were capped; they do not indicate full data coverage. No full-corpus run or
accuracy evaluation was performed.

Before deployment, verify actual delta generation and scheduler wiring, then
exercise dated ADD/UPDATE/DELETE batches and retry behavior. No scheduler was
activated here. Before any commit, follow docs/agent/tests-and-deps.md: update
affected tests and run the suite. The old rule-engine/service tests were retired;
30 updated media contract tests and four processing tests passed. The full suite
has not completed: `vv-env` lacks scikit-learn for media test collection, and a
separate run of the other tests was interrupted during slow browser tests. The
migration must also be reviewed and applied in
each target environment.
