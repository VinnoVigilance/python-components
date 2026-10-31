# PDG-115 adverse-news research data

## Final classical ML recommendation and blind model audit (2026-10-29)

The completed research recommendation is
[FINAL_TRADITIONAL_ML_RECOMMENDATION.md](results/FINAL_TRADITIONAL_ML_RECOMMENDATION.md).
GPT-6 Astra at high reasoning effort reviewed 100 blinded full-text articles in a
new task: 60 stratified recent examples and 40 intentionally difficult
publisher-transfer cases. Its judgments, evidence and uncertainty flags are in
`data/review/strong-review-v1/`, separate from the 5,000 silver targets. The
primary candidate is LSA 200 + RBF SVM C=3 at default score threshold zero,
reflecting the priority on unseen publishers; LSA 100 C=3 and character linear
SVM remain challengers. Lower thresholds and
simple voting were measured, with the recall/review-load trade-off documented.
The audit is a second model opinion and does not establish human-reference or
production accuracy. A full-data research bundle of the three finalists is in
`results/finalist-models-v1/`, with `predict_finalists.py` for scoring new JSONL
articles. The bundle is not approved for automated production decisions.

## Expanded model comparison (2026-10-29)

The 5,000-row proposed-label pool has now been screened with 46 classical
configurations across broad families and targeted refinements. Group-aware folds
used 4,053 pre-2025 articles; a chronological holdout used 947 articles from
2025–2026; six sufficiently sized older publishers were held out one at a time.
The leading family is LSA plus RBF SVM, with 100- and 200-component C=3 variants
remaining close finalists. UK_GOV transfer is weak, so the results do not
establish production accuracy. See
[the expanded screen report](results/EXPANDED_MODEL_SCREEN_REPORT.md) and
[the complete model ranking](results/expanded-screen-v1/model-comparison.csv).

## Current model-screen status (2026-10-28)

Exploratory training has now been run with the user's approval: all 62 classical
configurations completed 5-fold automatic-group CV and a separate eight-fold
leave-one-publisher-out evaluation. The first favored hybrid TF-IDF linear models
(mean F1 near 0.90); the source-held-out scores were lower and less stable (best
mean publisher F1 0.763 for LSA logistic regression). This is evidence that
publisher transfer is the harder task, not a production accuracy estimate.

See [the model screen report](results/MODEL_SCREEN_REPORT.md) and its CSV
rankings; those experiments used the original 500-row pool. The diverse 5,000-row
proposed-label corpus is now assembled below, but it is still mostly teacher
derived and no independent answer-blind human reference set exists. Its size
supports broader model comparison; it does not establish label correctness or
production accuracy. Historical notes below that say training has not started
are retained as dated history and are superseded by this update.

## Final 5,000-row adverse-news label pool (2026-10-29)

The user authorized labels for 4,500 more articles with GPT-6 Luna / low
reasoning effort. Full-text packets are prepared in
`data/labeling/expansion-4500-v2/` (477 batches, up to 10 articles each). The
selection covers all 10 available datasets and 9 publisher sources; one unique
ADB group is available after deduplication. It uses source-balanced quotas and
year/month sampling within larger sources, excludes the reference-reserved pool,
and preserves 3,641 provisional training and 859 provisional dev assignments.
Those split assignments remain provisional pending case review.

Task `01a182cf-17dc-77d2-8010-d5edbcb4bd14` completed all 477 packets. The
checkpoint and immutable raw returns are under
`data/labeling/expansion-4500-v2/returns/gpt6-luna/`. Raw expansion labels are
2,486 negative / 2,014 positive; two separate parent policy-QC corrections
produce 2,488 negative / 2,012 positive. The combined file is
`data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl`; its
manifest records source/dataset counts, language metadata, label origins and
integrity hashes. The 5,000 rows have unique prepared groups and content hashes.
All 10 datasets and nine sources are represented, including one deduplicated
ADB case. Source quotas are balanced, so the label distribution is not a
database-wide prevalence estimate. Language is unknown for 4,199 records.

This is a complete proposed-label pool, not an independent benchmark. No
answer-blind human reference labels are included, and same-case review and final
case-safe splits remain open. Do not interpret teacher labels as ground truth or
the provisional split tags as final evaluation folds.

Start with **[START_HERE.md](START_HERE.md)** for the current files and exact
human/DeepSeek/ChatGPT labeling instructions. The authoritative data snapshot is
now **`data/prepared/db-selection-v3/`**. Version 2 below is the retained historical
snapshot from before the UK metadata merge; use version 3 for subsequent work.

## Original 500-row pilot history

The original 100-article pilot and a 400-article paired DeepSeek/GPT expansion are
complete. The original four disagreements were accepted as 1 by the user; all 18
new expansion disagreements were also adjudicated after answer-hidden review.
The expansion's raw model agreement is 382/400 (95.5%), not accuracy. Across all
500 articles, labels are 261 positive / 239 negative: 473 proposed silver model
agreements and 27 user-reviewed/adjudicated cases. DeepSeek's model/mode was not
provided. See [START_HERE.md](START_HERE.md) and
`data/labeling/final-pilot-500-v1/` for current status and the joined text/label
pool. No training or CV has started; the labels do not constitute a production
benchmark. An independent, answer-blind human reference set remains necessary.

Sections below preserve the original preparation history and earlier interim
status; follow the current status above when they differ.

## Final preparation update (2026-10-28)

The current checkout is on the existing `research` branch. The user-provided UK
metadata CSV is preserved as `data/raw/adverse-news-uk-metadata-5000.csv`. All
5,000 physical IDs were found and their logical IDs, versions, sources, datasets
and database content hashes matched the article export. URLs/publication dates
are now present; all 5,000 UK language values remain null in the database.

`select_exports.py --metadata ...` created `data/prepared/db-selection-v3/`.
It retains 37,732 eligible candidates, 265 blank-body diagnostics and the 6,809
working selection. Input/output hashes are recorded. UK metadata did not change
article text or the working-selection size. Same-case/near-duplicate review and
split finalization remain pending.

`build_labeling_packets.py` created `data/labeling/pilot-v1/`: 100 unlabeled training
candidates, 21 self-contained packets of up to five full articles, plus optional
blind human-review files for five and five more. A long batch is split rather than
truncated. The preserved policy is adapted only for multi-article transport with
an opaque article ID per output line. Session provenance includes prompt/policy,
packet, mapping and source snapshot hashes. No model calls or labels were made.

`import_batch_labels.py` is ready to validate real saved website answers and
preserve proposed silver labels. It does not accept arbitrary IDs, extra fields,
duplicate responses, non-integer labels or missing answers. It has not been run
against fabricated responses. Human review and model output remain separate.

Reproduce into **new** directories, from the repository root:

```powershell
& 'C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' `
  research/adverseNews/select_exports.py `
  --inputs research/adverseNews/data/raw/adverse-news-non-uk.csv `
           research/adverseNews/data/raw/adverse-news-uk-sample-5000.csv `
  --metadata research/adverseNews/data/raw/adverse-news-uk-metadata-5000.csv `
  --output research/adverseNews/data/prepared/db-selection-v3 `
  --per-dataset 2000 --as-of 2026-10-28

& 'C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' `
  research/adverseNews/build_labeling_packets.py `
  --snapshot research/adverseNews/data/prepared/db-selection-v3 `
  --output research/adverseNews/data/labeling/pilot-v1 `
  --count 100 --batch-size 5 --max-characters 60000
```

Execution stops at preparation. Labeling decisions will be discussed after the
first real batch; no training, CV, model inference, tests, installs or commits.

## Current scope and authorization

The 500-article labeling pool has been prepared and adjudicated as noted above.
Training/CV still require explicit approval. The 473 model-agreement labels remain
silver, and the 27 user-reviewed/adjudicated labels are not an independent
production reference. Do not convert either status into an accuracy claim.

The target remains integer `0` or `1`. Filtered unusable articles have no target.
Article adversity does not imply every named actor is guilty. Risk-category
predictions, categories, source identities and generated explanations are never
semantic input features. Only the full title and body are model text.

Accepted policy/design: `../../docs/PDG-115-adverse-news-labeling-guide.md` and
`../../docs/PDG-115-cpu-research-and-training-design.md`. A preserved policy/prompt
snapshot is now available in `policy/adverse-news-policy-v0.2/`; the annotation
execution workflow remains future work.

## Authoritative supplied files

- `data/raw/adverse-news-non-uk.csv`: all exported current non-UK records,
  32,997 rows. Raw payload retained.
- `data/raw/adverse-news-uk-sample-5000.csv`: 5,000 UK records selected by the
  supplied seeded MD5 ordering of `vv_media_id`; compact export without payload.

Raw exports remain unchanged. SHA-256 hashes, input record counts and output hashes
are recorded in `data/prepared/db-selection-v2/`. `data/` is Git-ignored.

## Prepared selection (2026-10-28)

Use **`data/prepared/db-selection-v2/`**:

| File/directory | Purpose |
| --- | --- |
| `working-candidates.jsonl` | 6,809 unlabeled articles for subsequent work, with full title/body |
| `by-dataset/*.jsonl` | Complete 37,732 conservatively eligible articles across 10 datasets |
| `diagnostics.jsonl` | One diagnostic per input row, including the 265 blank bodies |
| `filtered-records.jsonl` | Filtered identities and reasons, without a label |
| `automatic-groups.jsonl` | 37,653 automatic connected groups, 71 containing multiple records |
| `provisional-splits.jsonl` | Candidate split/fold assignments; case review is still pending |
| `preview-one-per-dataset.jsonl` | Nine exploratory training-candidate articles; optional future review discussion |
| `inventory.json` | Counts, source/language/date/length inventory and limitations |
| `snapshot.json` | Preparation-code and output-file hashes |

The working selection includes at most 2,000 distinct-body representatives per
dataset. PTV and UK are capped at 2,000 each. Smaller sources retain available
representatives. Representative choice is seeded across the combined eligible
pool, so a repeated body can be represented by another source. All original
eligible records and their source provenance remain in `by-dataset/`.

This cap is a convenient working-pool choice, not class balancing, a training-size
requirement or a representative sample of production traffic. No adversity labels
or prevalence estimates exist. Missing titles and short complete articles are
retained. Eligibility is conservative and still needs extraction/language review.

## Metadata handling

The SQL exports encode nulls as literal `NULL`. The selector decodes exact null
sentinels before grouping, while leaving the raw CSV files intact. Full-body text
is preserved; it is not truncated, lowercased or stripped of negation.

In the non-UK payload, `Sources` and `Dates` are arrays and language is under
`Metadata`. The selector recovers URL and publication dates from those arrays,
and language where supplied. The original flattened query paths did not account
for that structure. Title/body columns are checked against the preserved payload.

UK has no payload fallback. Its URL/date/language metadata remains missing.
Publication-date checks involving UK require a later **metadata-only** export for
the selected IDs. `data/requests/uk-5000-metadata.sql` now contains a read-only
query for the exact 5,000 physical IDs in the original export. Run it in the DB
query tool and export all results as UTF-8 CSV to
`data/raw/adverse-news-uk-metadata-5000.csv`. It preserves missing rows with
`record_found=false` and does not resample or restrict old versions to current.
Do not re-download full UK bodies. Do not infer English from an
unknown field, and do not use ingestion time as publication time.

Across eligible articles, language metadata is missing for 36,779 and reports `en`
for 953. One possible corrupted extraction is flagged for inspection, not deleted.

## Grouping and split boundaries

Automatic grouping links physical versions with the same `vv_media_id`, same
source/dataset record key, exact nonempty URL, or identical complete body after
Unicode NFC/whitespace normalization. Links are transitive. Group identifiers hash
the sorted member IDs. Missing URLs never create links.

This catches exact copies and known identity links, **not paraphrases, near copies
or different articles about the same case**. Group assignments must be revised
when those relationships are reviewed. The provisional split file is explicitly
not a frozen, case-safe final benchmark.

Seeded group hashing proposes 70% training candidates, 15% development candidates
and 15% independently reviewed reference candidates. These are group proportions;
article counts differ. Training candidates receive one of five provisional CV
folds. No fitting or validation runs took place. Reference candidates are excluded
from training and development even though they do not yet have reference labels.

`configs/robustness-plan.json` preserves the user's concern about format transfer:

1. Group-aware cross-validation (CV): rotate training groups through held-out folds;
   fit vocabulary and classifier inside each fold, after training is approved.
2. Held-out publisher comparisons: exclude an entire `source_name` from fitting and
   purge related groups crossing that boundary. PCIJ datasets share a publisher.
3. Later-date comparison: use actual publication dates and keep related cases
   together. A date boundary cannot be selected credibly for UK yet.

These answer different questions. CV alone does not establish robustness to an
unseen publisher. Limited human labels mean results will initially be exploratory,
even if future CV or teacher agreement looks strong.

## Reproduce preparation only

Python standard library only; no installation, model or production imports:

```powershell
& 'C:/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' `
  research/adverseNews/select_exports.py `
  --inputs research/adverseNews/data/raw/adverse-news-non-uk.csv `
           research/adverseNews/data/raw/adverse-news-uk-sample-5000.csv `
  --output research/adverseNews/data/prepared/db-selection-v2 `
  --per-dataset 2000 --as-of 2026-10-28
```

Run from the repository root. Use a **new output directory** for another run:
existing outputs are never silently overwritten. CSV supports quoted multiline
bodies. Input hashes are checked before/after reading, and derived JSONL excludes
the redundant raw payload and taxonomy. Each prepared article retains its input
filename/record number and source/version identity.

`prepare.py`/`contracts.py` are earlier general scaffold drafts for canonical or
flat JSONL inputs and annotation validation. They are not the entry point for
these CSV exports; the CSV and generic annotation formats still need an explicit
bridge before annotation execution. `configs/experiments.json` describes E1/E2
interfaces, not executable training.

## Stop point

Prepared candidates and provisional reservation files are ready for discussion.
No human homework is required now. A few optional reviewed examples can clarify
policy later; they cannot establish a release-quality reference. Do not launch
training, CV, teacher labeling/inference, downloads or installation until the
user approves the next phase. No implementation tests were added or run.
