# PDG-115: start here for version 2

## What exists now

Version 1 is the fast **LSA 200 + RBF SVM C=3** article classifier. The
frozen `model/model.joblib` was fitted on 5,044 articles with title and full
body as its only inputs. The complete, compressed training rows are in
`training/final-5044.jsonl.gz`; `training/base-5000.jsonl.gz`, the six policy
corrections, and the 44 reviewed additions explain how they were assembled.
The base dataset manifest records publisher/source, dataset, label-origin,
and language coverage. `code/assemble_training.py` and `code/fit_model.py`
document the exact assembly and fit parameters. `model/research-manifest.json`
records the frozen artifact hash and evaluation metrics.

The deployed decision policy has **not** been changed by this research work.
The model is suitable for prioritizing a full-text review queue only. Every
article, including a model-negative one, still needs review. UK_GOV, PTV, and
unseen source names get explicit full-review routes in `code/score_articles.py`.
New publisher organizations within a known source do not have a separate
registry yet; the all-review rule covers them in v1.

## What the results mean

The newest completed, previously reserved UK_GOV packet contains 35 full
articles. Against GPT full-text opinions, v1 had 31/35 agreement, accuracy
88.6%, precision 100%, recall 50.0%, and F1 66.7%. It found four of eight
GPT-positive articles. The older 117-article UK packet is diagnostic because
its errors guided the 44 training additions. The older 150-article packet
mixes sources. Their results are in `EVIDENCE.md`, with per-article predictions
in `model/comparison.csv`. All these labels are model opinions, not an
independent human accuracy reference.

The fresh 400-article UK_GOV packet was selected from the user's 500-row
export before reading model scores. Its articles, selection metadata, and
source export are preserved in `evaluation/fresh_400_in_progress/`. Only
**60/400** GPT full-text reviews have been saved. Do not score that packet or
add it to training until the review is complete and frozen. Resume at article
61 (`db:37396`); see its `PROGRESS.md`.

## Key product decisions

- Classify the **article**, not whether each named actor is guilty. A
  specific alleged crime, concrete qualifying abuse, formal investigation
  or enforcement, or actual target-specific sanctions measure can make an
  article positive. A keyword or negative tone alone does not.
- A UK school/council improvement notice alone is negative unless the
  supplied article describes a specific qualifying breach or enforcement
  basis. Do not infer content from an unavailable annex.
- Read title **and the entire body**, including the end. Record an exact
  article substring as evidence for a positive GPT review.
- The source-balanced 5,000-row training pool is not an estimate of
  production prevalence. Different evaluation packets are not a score trend.
- Do not train on an evaluation packet and then report its score as an
  unseen-data result. For a v2 update, label **separate** training articles
  and keep another untouched evaluation packet.

## Suggested v2 sequence

1. Finish and freeze the 400 blind GPT reviews. Adjudicate the uncertain
   cases before revealing any model predictions.
2. Use `code/evaluate_packet.py` to inspect misses and false alarms by
   article type and `publisher_name`. Compare the errors with the diagnostic
   reports in `context/`. The 35-case UK packet already shows misses involving
   counterfeit medical devices, prolonged political detention, child abuse
   buried in a briefing, and a certification suspension.
3. Obtain independent human labels for a fresh live-arrival sample, especially
   model-negative articles, and agree on the maximum acceptable adverse-miss
   rate. GPT agreement alone does not establish production accuracy.
4. Choose a disjoint v2 training batch targeting confirmed error patterns
   while preserving ordinary UK_GOV and PTV examples as controls. Keep the
   selected article IDs, source/publisher metadata, full text, labels,
   evidence, and label origins together.
5. Refit the fast model and compare against v1 on a new untouched packet.
   Consider a teacher-student model only if the fast model still misses
   important patterns after enough diverse, well-reviewed training examples.

## Fresh-device setup

Clone the repository and check out the `research` branch. Install Git LFS and
run `git lfs pull` so `model/model.joblib` is the actual 126 MB artifact and
not only its pointer. Install the pinned packages in `requirements.txt` in a
Python environment. The files in this folder are sufficient to load and
score v1, rebuild its training rows, refit from those rows, and continue the
400-article review. The SQL in `queries/` records how UK_GOV samples and
source counts were requested; database access is required to fetch new rows.

The `context/` folder retains compact reports on classical-model selection,
publisher transfer, UK_GOV diversity, and the school-notice policy change.
It also retains the frozen encoder comparison, which found no publisher-transfer
gain over LSA + SVM. It omits large intermediate models, caches, and exploratory
per-fold outputs.

`legacy/` preserves the original exploratory scripts and notes in case a v2
investigation needs to trace how the early packets or screens were generated.
Those scripts are historical and may depend on local extracts that were not
uploaded. Use `code/` for the supported v1 workflow.
