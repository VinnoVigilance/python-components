# PDG-115: adverse-news modeling

## Updated UK candidate packaged; review-assisted pilot only (2026-10-31)

Read `results/uk-followup-candidate-v1/FINAL_DECISION.md` first. From the
unused 1,500-row UK_GOV export, 35 articles were reserved before scoring and
36 separate training candidates were fully GPT-reviewed. Ten uncertain
training opinions were excluded. The refit uses 5,044 rows: original 5,000,
six policy corrections, 18 earlier certain additions, and 26 new certain
additions. The updated bundle is
`results/uk-followup-candidate-v1/candidate-bundle.joblib` (scikit-learn
1.9.1). The research review router uses this bundle by default and retains
`auto_clear=false` for every article; the original bundle remains available.

On fresh reserved articles, the updated model matched 31/35 GPT opinions,
versus 30/35 for the original. Both found 4/8 GPT-positive articles, so
fresh adverse recall did not improve. The updated model improved on the old
117-article packet, but that packet's misses guided training selection, so
the gain is diagnostic. No production accuracy claim or automatic-clearance
release is supported. The next release gate requires a new live-arrival sample
and independent human adjudication, especially of negative predictions.

## Random UK_GOV shadow comparison completed (2026-10-31)

Read `results/uk-random-shadow-v1/SHADOW_REPORT.md` first. The user supplied
the requested 120-row random UK export. After excluding three old-export
overlaps, 117 full articles were reviewed by GPT before revealing either
model's predictions. Both models agreed with 106/117 GPT opinions (90.6%,
F1 0.645) and both missed 8/18 GPT-positive articles. The experimental
update changed only two predictions: one certain school-notice false alarm
was fixed and one uncertain historical-war false alarm was added. Keep the
original model as current candidate; do not promote the experiment. These
are GPT agreement figures, not independent human production accuracy.

Next: select a separate UK training batch that covers the six certain missed
crime/abuse patterns, label full text with evidence, exclude uncertain cases,
refit, and then obtain a **fresh** random UK evaluation packet. Do not train
on or tune to the 117 frozen articles. Keep new-publisher/low-confidence
articles in full-text review while evidence remains limited.

## Experimental UK candidate and completed random check (2026-10-31)

The improved model is packaged separately in
`results/uk-diversity-candidate-v1/experimental-bundle.joblib`; its manifest
records the 5,018 training rows, six policy corrections, scikit-learn 1.9.1
runtime, and exact reproduction of the previous 171 held-article scores. It is
for research/shadow comparison, not production promotion. Read
`results/uk-diversity-candidate-v1/NEXT_SHADOW_CHECK.md` for the full flow. The
completed random-check input was the UTF-8 CSV output of
`data/requests/uk-random-shadow-check-v1.sql`, a read-only fixed random sample
of 60 all-time and 60 recent UK_GOV articles. GPT full-text opinions were
frozen before comparing both model predictions. The original model remains intact.

## UK_GOV small training experiment (2026-10-31)

Read `results/uk-diversity-update-v1/EXPERIMENT_REPORT.md` for the current
experimental result. The 21-article UK diagnostic packet stayed outside
training. A separate 21-article packet was fully GPT-reviewed; three uncertain
cases were excluded, and 18 certain opinions were added to an experimental
copy of the 5,000-row corpus after applying six product-confirmed UK notice
label corrections. All three model variants were refitted under the same
scikit-learn 1.9.1 runtime. Agreement on the 20 certain new UK diagnostic cases
rose from 13/20 (F1 0.364) to 18/20 (F1 0.800), while the 132 certain older
reserved cases kept identical predictions (F1 0.923). This is a small,
model-opinion-based comparison, not production accuracy. The original model and
dataset are unchanged; the refit has not been packaged or deployed.

## New UK_GOV export and blind GPT diagnostic (2026-10-31)

The user supplied a 1,500-row full-payload UK_GOV export from
`data/requests/uk-diversity-sample.sql`. It adds 1,443 logical articles absent
from the earlier 5,000-row UK raw extract and exposes publications, case studies,
speeches, fatalities, and medicine alerts. Read
`results/uk-diversity-review-v1/REVIEW_REPORT.md` before further sampling or
training. A frozen 21-article, seven-section packet was fully read by GPT before
revealing model scores. It found seven certain model/GPT disagreements: three
missed positives and four false alarms. This is targeted model disagreement,
not accuracy or F1 against independent people. The SQL's medicine URL section
grouping makes the 1,500-row export intentionally unsuitable for estimating
source-wide prevalence. No training change has been made. Keep this packet as a
diagnostic holdout; select separate training examples for the next experiment.

## Focused review update (2026-10-31)

Read `results/pattern-training-v2/REVIEW_REPORT.md` and
`results/focus-review-v2/UK_NOTICE_POLICY_AUDIT.md` for the latest decision.
The corrected 18-article UK/PTV packet had zero overlap with the original
5,000-article pool and was fully reviewed by GPT. All 14 certain GPT opinions
agreed with the current SVM decision, so this packet supplied no clear new
training errors; no retraining was performed. Four selected records have a
provisional `dev_candidate` split and must not be added to training. These
opinions do not establish production accuracy.

The product owner confirmed that a UK school/council warning or improvement
notice needs a specific qualifying breach or enforcement basis in the supplied
article. Six existing model-proposed positives in the 5,000-row pool are
inconsistent with that rule; the proposed 1→0 corrections are recorded in
`results/focus-review-v2/uk-notice-policy-corrections.csv`. The original corpus
and evaluation runs are frozen. Future experiments may apply this overlay
explicitly and report that they did so.

For broader UK_GOV coverage, run the read-only query
`data/requests/uk-diversity-sample.sql` and export its result as a UTF-8 CSV.
It selects up to 1,500 distinct articles across publication years and URL
sections, with the full saved payload for title/body extraction. This is the
next external input needed before assessing whether the current local 5,000-row
sample misses document types in the 143,757-article source.

## Final traditional ML research recommendation (2026-10-29)

Read `results/FINAL_TRADITIONAL_ML_RECOMMENDATION.md` first. A fresh GPT-6 Astra
high-reasoning task blindly reviewed 100 selected full-text articles with exact
evidence quotes and uncertainty flags. The recommendation is LSA 200 + RBF SVM
C=3 at default threshold zero as the primary research candidate, reflecting the
priority on unseen publishers, with LSA 100 C=3 and character linear SVM as
challengers. Threshold and voting comparisons
are complete. The stronger model's judgments remain separate from the 5,000
silver targets. Independent human reference, same-case review, and final
case-safe splits are still required to establish production accuracy.

## Expanded model comparison complete (2026-10-29)

The 5,000-row proposed-label pool has been screened across 46 classical
configurations: 22 broad family representatives, 18 parameter refinements, and
six LSA dimension variants. The evaluation used three group folds on 4,053
pre-2025 articles, 947 newer articles as a chronological holdout, and six
publisher holdouts. LSA plus RBF SVM leads publisher transfer, but UK_GOV and
PTV remain difficult. Read `results/EXPANDED_MODEL_SCREEN_REPORT.md` and the
full `results/expanded-screen-v1/model-comparison.csv` before choosing a model.
These scores are against proposed labels and cannot establish production
accuracy without an independent blind human reference.

## Final 5,000-row adverse-news label pool (2026-10-29)

The user authorized 4,500 additional labels from GPT-6 Luna at low reasoning
effort. The complete-text selection is in `data/labeling/expansion-4500-v2/` as
477 packets of up to 10 articles. It spans all 10 available datasets and nine
publisher sources; only one distinct ADB group remains after duplicate removal.
Selection is source-balanced subject to available non-reserved unique groups,
and stratified over year/month within larger sources. The reference-reserved
pool was not used. Of the new 4,500 rows, 3,641 are provisional training
candidates and 859 provisional development candidates. These split tags are
provenance, not frozen case-safe final splits.

All 477 packets were labeled in task
`01a182cf-17dc-77d2-8010-d5edbcb4bd14`. The checkpoint is
`data/labeling/expansion-4500-v2/returns/gpt6-luna/progress.json`; immutable raw
packet returns and two policy corrections are stored alongside it. Raw GPT
counts for the expansion are 2,486 negative / 2,014 positive. After the two
corrections, they are 2,488 / 2,012. The combined dataset is
`data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl`, with
provenance and coverage in `manifest.json`. It contains 5,000 unique content
hashes and prepared groups, with 2,727 negative / 2,273 positive labels. All 10
datasets and nine publisher sources are represented; one ADB group remains
after deduplication. Source quotas were balanced among eligible rows, so label
prevalence is not an estimate of prevalence in the database. Language is known
for 801 records and unknown for 4,199; unknown values are preserved.

This is a complete proposed-label pool, not an independent benchmark. The 4,500
new labels are GPT-6 Luna proposed silver except for two explicitly recorded
policy-QC corrections. No independent blind human reference is included, and
same-case review and final case-safe split construction remain incomplete. The
5,629-group reference-reserved pool remains untouched. Read the final manifest
before model training or evaluation.

## Current modeling status (2026-10-28)

The user approved exploratory classical-model training and comparison. All 62
configured CPU-friendly model variants completed on the 500-row label pool.
Five-fold automatic-group CV favored hybrid word/character TF-IDF linear models
(best mean F1 about 0.904). An additional leave-one-publisher-out screen favored
LSA logistic regression by mean publisher F1 (0.763); the RBF SVM was close and
had higher mean recall and average precision. Scores fell substantially and
varied across publishers, so same-source group CV alone overstates transfer.

Read `results/MODEL_SCREEN_REPORT.md` for the comparison and interpretation;
machine-readable rankings are in `results/classical-screen-v2/` and
`results/source-heldout-screen-v1/`. Only title/body entered models. The labels
are mostly teacher-derived (473 agreements, 27 discussed/reviewed cases), the
automatic groups and same-case review remain provisional, and there is no
independent answer-blind human reference. These results are exploratory and do
not establish production accuracy. A diverse 5,000-row proposed-label corpus is
now available, but its teacher labels still need case review and comparison
against an independent answer-blind human reference.

The next modeling steps are: review duplicate/case links, freeze group-safe
development and publisher holdouts, create a separate blind human reference
set, and rerun top linear/SVM/NB/tree families with repeated group folds and
publisher slices on the expanded pool. Then decide whether an assisted-review
pilot is justified.
Do not use the current CV scores as a production reliability claim.

# PDG-115: first labeling batch

## Paired expansion and disagreement adjudication complete (2026-10-28)

The user skipped the six-case agreement audit and approved using both DeepSeek and
GPT for the next 400 articles. The original four disagreements (015, 045, 078,
087) were all resolved to 1 after discussion; the other 91 model agreements remain
proposed silver. Original 96/100 is the raw-model agreement before adjudication,
not accuracy. Preserve these statuses and the original responses.

The paired expansion is `data/labeling/paired-expansion-400-v1/`: 400 additional
training-candidate articles, disjoint by automatic group from the first 100, using
the same frozen policy and identical packets for both labelers. DeepSeek's
user-pasted 400 labels and GPT-6 Luna's 400 labels were strictly imported across
41 packets of up to 10 articles. Pair comparison: 382/400 agree (95.5%); 18
disagreed (4 DeepSeek 1 / GPT 0; 14 DeepSeek 0 / GPT 1). The user assigned labels
for all 18 after answer-hidden review. Final labels for this expansion: 203
positive / 197 negative. The user's decisions matched DeepSeek on 16/18 selected
disagreements and GPT on 2/18; this disagreement-only comparison is not an
accuracy estimate. Raw model agreement remains agreement, not accuracy. DeepSeek
model/mode remains unknown. See
`data/labeling/paired-expansion-400-v1/comparison/summary.md` and
`final-labels-400.jsonl`. The raw aggregate DeepSeek response, per-packet
returns/imports, answer-hidden review, and user decisions are preserved. No
training or CV has started.

The 500-article pool is a teacher-labeled training pool, not sufficient by itself
to prove production reliability. Across both rounds, there are 473 model-agreement
silver labels and 27 user-reviewed/adjudicated labels after discussion. All 500
now have a binary training target, with origin preserved. These reviews were
selected from training candidates and/or discussed after model exposure, so they
are not an independent production reference. Before a production claim, assemble
a separate answer-blind human reference set from reserved groups/sources and
report performance by relevant slices; do not put those reference labels into
training. The skipped six-case agreement audit remains unused and intact.

The 500 labels support an initial exploratory baseline after explicit training
approval; cross-validation on these teacher-derived and discussion-adjudicated
labels measures fit to this labeling process, not production truth.

Combined 500-row training pool with full title/body, integer target, separate label
origin/status, hashes, and a manifest is at
`data/labeling/final-pilot-500-v1/training-pool-500-title-body-labels.jsonl`.
Counts are 261 positive / 239 negative: 473 proposed silver agreements and 27
user-reviewed/adjudicated labels. Feature extraction must use only title/body.
Training and CV remain unstarted pending explicit approval.

## Four disagreements resolved by the user (2026-10-28)

The user accepted label 1 for 015,045,078,087 with specific reasons/passages.
No disagreements remain unresolved. The original model outputs and human-first-5
file remain unchanged. `comparison-final/human-disagreement-adjudications.jsonl`
preserves these post-comparison decisions; `pilot-labels-with-review-status.jsonl`
contains all 100 labels (58 positive / 42 negative), distinguishing 9 human-
confirmed decisions after discussion from 91 proposed silver model agreements.
This is not a 100-article independent human reference. Original model agreement
remains 96/100, not measured accuracy. The user later skipped the proposed small
agreement audit and authorized moving forward; see the current status at the top.

## Pilot comparison complete (2026-10-28)

All 100 articles now have validated DeepSeek and GPT labels. Final report:
`data/labeling/pilot-v1/comparison-final/summary.md`. Model agreement is 96/100,
not verified accuracy. DeepSeek: 54 positive / 46 negative; GPT: 58 / 42.
Four disagreements (DeepSeek 0 / GPT 1): 015, 045, 078, 087. Their complete
articles and GPT evidence notes are in `comparison-final/review-disagreements.md`.
Neither model automatically wins. All machine labels remain proposed silver.
No more DeepSeek packets are needed for this 100-article pilot. Next is policy
review of disagreements plus some agreements, not training. DeepSeek model/mode
remains unknown because it was not supplied. Older progress sections below are
historical instructions; do not repeat completed batches.

## Current DeepSeek packets: up to ten articles (2026-10-28)

DeepSeek batches 001–003 from the original session are saved/imported: articles
001–015 are complete. GPT completed all 100 original pilot articles independently.
The first 15 have 14 agreements and one disagreement on AN-P115-015; retain it for
policy review, not automatic acceptance of either model. See the original
session's `comparisons/through-batch-003.json`.

Use **`data/labeling/pilot-remaining-10-v1/paste-packets/`** from now on.
There are 85 remaining articles in 10 packets with sizes
10, 7, 7, 10, 10, 9, 9, 9, 8, 6. The 60,000-character planning cap can create a
smaller packet; no body or policy text is truncated. The policy/transport is
unchanged. Original article IDs and GPT answers are preserved.

**Next:** paste `pilot-remaining-10-v1/paste-packets/batch-001-PASTE-THIS.txt`
into a fresh DeepSeek conversation. This new packet contains AN-P115-016–025.
Return its answer here or save under the **new** session's
`returns/deepseek/batch-001.txt`. Do not overwrite the original batch-001 response.
Do not continue the older five-article packets unless explicitly requested.
Use the new session path when importing future responses; compare GPT by article
ID across the original batch boundaries. Old packet files/manifests stay intact.

## Labeling authorization update (2026-10-28)

The user confirmed the first-five policy discussion and authorized GPT labeling
of the prepared 100-article pilot in a fresh chat. GPT work was dispatched to
chat `01a18214-3fd6-7492-a36b-2fb978a5a47a` (PDG-115 GPT pilot labeling).
It processes the 21 batches with full-text reading, concise evidence sidecars and
per-batch validation, without viewing human or DeepSeek answers. It uses the
chat's configured model; no paid/external API calls, model training or CV.

The user does not need to repeat the first human review or run GPT manually.
`human-confirmed-adjudications.jsonl` preserves accepted first-five decisions
separately from the unchanged original review. These were confirmed after AI
discussion and are not an independent blind final reference.

**Current user action:** paste batch 001 into a fresh DeepSeek chat as below.
For this quality-focused pilot, use reasoning/DeepThink if the website offers it,
keep search off, and record the actual displayed mode. This supersedes the older
economical standard-mode suggestion below. Save the final JSONL answer in
`returns/deepseek/batch-001.txt`; do not copy any separate reasoning panel.
GPT's separate chat is already handling the second-model work. Do not send it
DeepSeek answers. Training and expansion beyond these 100 remain unapproved.

The repository is on the existing `research` branch. All new adverse-news work is
in `research/adverseNews/`. Data preparation is complete for this snapshot. No
models were called during preparation; subsequent authorized GPT labeling has
started as described above. No training/CV or commit has occurred.

## What is ready

- `data/prepared/db-selection-v3/`: authoritative prepared snapshot, now including
  the exact-ID UK metadata overlay. 37,997 input records, 37,732 nonblank-body
  candidates, 265 blank bodies recorded without labels, 6,809 working candidates.
- `data/labeling/pilot-v1/`: 100 training-candidate articles in 21 website packets,
  at most five articles per packet, with a 60,000-character planning limit.
- Full title/body is preserved. A long article is never silently truncated.
- Raw exports, packets and returned answers are Git-ignored. Source code, policy,
  instructions and configuration can be reviewed separately from the private data.

## Recommended first action: at most five human judgments

Open `data/labeling/pilot-v1/human-first-5.md` and apply
`policy/adverse-news-policy-v0.2/labeling-guide.md`. Read it before model answers.
Reply in this chat with the article ID, integer 0/1 and a brief reason or exact
passage. For example, use the structure `ARTICLE_ID = YOUR_LABEL; reason ...`.
Do not treat a sample format as an assigned label. Skip this step if you cannot
spend time now; it is not a prerequisite to the first model trial. A second five
are in `human-optional-next-5.md` only if convenient.

These few judgments help discuss policy application. They are exploratory, not
enough for a production accuracy claim or an independently adjudicated final set.

## What to paste into DeepSeek

1. Open `https://chat.deepseek.com/` and start a new conversation.
2. Turn web search off. For the initial economical trial use standard/non-thinking
   mode if available. Record the actual displayed model and mode; if the site does
   not expose a revision, record that honestly. Use a consistent mode across batches.
3. Open `data/labeling/pilot-v1/paste-packets/batch-001-PASTE-THIS.txt`.
4. Copy the **entire file** into one message and submit. The file already contains
   the full accepted policy, examples, strict response instructions and five full
   articles. Do not add a second prompt or upload either large database CSV.
5. Expect one JSON object per line, with exactly `article_id` and integer `label`.
   JSONL means one complete JSON object per line. There must be five IDs, each once.
6. Copy the complete answer into Notepad and save it as UTF-8:
   `data/labeling/pilot-v1/returns/deepseek/batch-001.txt`.
7. Tell this chat the file is ready and report the displayed model/mode. If saving
   is awkward, paste the full answer here instead.

The website may offer a downloadable file, but this workflow does not rely on
that. A text response saved as `.txt` is sufficient; the importer validates and
converts it into the research annotation format. Do not fill missing answers with
0, silently remove an ID, or copy an explanation into a label field.

**Do only batch 001 first.** After checking response validity and policy behavior,
we can continue gradually. Each later packet needs a **new conversation** and the
entire packet pasted again. Do not carry the previous answer into the next batch.
Smaller batches simplify review; they are not proof that the model attends better.
If a response is incomplete, we will preserve the failure and split/retry it.

## Second model

For a clean manual comparison, paste the identical packet into a **fresh ChatGPT
chat**, without showing DeepSeek's answer. Record the actual model selected in the
UI; do not assume a website model is identical to a named API model. Save its
complete answer as `data/labeling/pilot-v1/returns/chatgpt/batch-001.txt`.

No API key or API spending is needed for this manual website workflow; access and
message limits depend on the accounts. Automated API labeling remains a separate
decision requiring provider setup and an agreed budget. API recommendation:
compare a small DeepSeek Flash / GPT-6 Luna pilot before bulk use. Do not infer
that a free website means an unlimited free API.

If you prefer a second AI assessment in this current chat, request it **before
sharing DeepSeek's answers**. It remains AI review, not a human reference.

## What this assistant does after answers arrive

1. Preserve raw responses and displayed model/mode information.
2. Validate every expected ID, exactly one response per article, and integer 0/1.
   Reject invalid, duplicate or incomplete output; errors are never negative labels.
3. Import labels as `proposed_silver` with article text hash, policy/prompt versions,
   batch hash, provider/model and raw-response hash. No automatic human-reference
   designation or accepted-label promotion.
4. Compare the two responses and any independently supplied human judgments.
   Inspect both disagreements and some agreements. Agreement alone is not accuracy.
5. Discuss continuing the labeling batches. Training stays paused until approval.

The ready importer is `import_batch_labels.py`. It is not necessary for you to run
it; return the answer files and the assistant can handle the import. No response
files have been manufactured, and the importer has not been run without real data.

## Prompt policy and boundaries

Base snapshot: `adverse-news-policy-v0.2`, prompt `pdg115-binary-labeler-v0.2`.
Website transport: `pdg115-website-batch-v1`. The business definition is unchanged.
The transport loops the same article policy over a small batch and adds an opaque
article ID so answers can be matched. Each article's own title/body is the sole
semantic input. Policy examples are illustrative, not batch answers.

The full prompt includes specific attributed allegations, relevant formal
investigations/enforcement and actual official restrictions; the exclusions;
resolution-only handling; article adversity versus actor guilt; full-body reading;
and protection against instructions embedded in article text. There are only
integer targets 0/1. Unusable input has no target. No risk-category output or model
result is needed. Metadata and teacher explanations never enter classifier text.

Packets are selected from training candidates only, one automatic group each,
with dataset coverage rather than production proportions. No reference-reserved
or development candidates are sent. The 100-article comparison pool covers nine datasets;
ADB's small working pool has no training-candidate group under the current
reservation, so no ADB reserved article was borrowed merely to fill coverage.
Near-duplicate/case review is pending; partitions are still provisional.

For later experiments, group-aware CV, publisher holdouts and publication-date
holdouts remain planned. They have not run. UK URL/date gaps are repaired; language
is still unknown for most rows because it is missing from the database itself.
