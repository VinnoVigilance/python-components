# PDG-115: final classical ML research recommendation

## Decision

Use **LSA 200 components + RBF SVM, C=3, at its default score threshold of 0**
as the primary research candidate. This follows the user's priority of transfer
to an unseen publisher. Keep **LSA 100 + RBF SVM, C=3** as the close challenger
and **character TF-IDF + linear SVM, C=1** as a simpler fallback. The 0.009
publisher-F1 lead is small, so it is a priority-based choice rather than proof
of a superior model. This is an initial operating point for further evaluation,
not certification for automated production decisions.

The primary scored F1 0.908 in three group folds on pre-2025 news, 0.811 mean
F1 when six older publishers were withheld one at a time, and 0.932 F1 on
2025–2026 news. The 100-component challenger scored 0.906, 0.802 and 0.937
respectively. The primary had 38 false negatives and 40 false positives among
947 recent articles *relative to the silver labels*; the challenger had 35 and
37. The character model had lower publisher-transfer mean F1 (0.778).

The complete broad, refinement and dimension search is in
`EXPANDED_MODEL_SCREEN_REPORT.md`; all 46 configurations and 394 fits are in
`expanded-screen-v1/model-comparison.csv`. These outcomes use proposed labels,
mostly created by other language models. They do not measure true production
precision or recall.

## Stronger blind model review

The user asked for a stronger model/effort in a new chat instead of a large
human-labeling task. A fresh **GPT-6 Astra, high-reasoning** task reviewed 100
full-text articles, using only the frozen policy and blinded packets. It saved
one binary judgment, an exact evidence quote or empty string, a policy reason,
and an uncertainty flag per article. Its batch outputs, checkpoint and joined
labels are under `../data/review/strong-review-v1/returns/astra-high/`.

Selection comprised 60 randomly sampled 2025–2026 articles, stratified across
all nine sources, and 40 targeted pre-2025 transfer cases. All three ML
finalists disagreed with the existing silver label on each targeted case by
selection. The targeted group concentrates 20 UK_GOV and 15 PTV articles plus
five from other publishers. The sampling is useful for error discovery, not an
unbiased estimate of dataset label error or model accuracy.

| Review slice | Articles | Astra positive | Astra vs existing silver | LSA 100 vs Astra F1 | LSA 200 vs Astra F1 | Character SVM vs Astra F1 |
|---|---:|---:|---:|---:|---:|---:|
| Stratified random recent | 60 | 34 | 2 disagreements | 0.909 | 0.912 | 0.879 |
| Deliberately difficult transfer | 40 | 22 | 19 disagreements | 0.488 | 0.488 | 0.488 |

Across the 40 targeted cases, Astra sided with the three unanimous ML outputs
in 19 and with the old silver labels in 21. Seven of the 100 Astra judgments
were marked uncertain. The join and all case-level comparisons are in
`../data/review/strong-review-v1/comparison/review-comparison.csv`; the 21
silver disagreements are separately listed in `silver-disagreements.csv`.

Astra is a second model opinion, **not human ground truth**. It also made some
policy-boundary calls that need correction or adjudication: it treated a Show
Cause Order for a missing price tag as qualifying enforcement, although prior
user adjudications excluded such preliminary orders; and it treated a proposed
cyber-libel complaint as included conduct, although the prior user decision
excluded cyber libel from this policy. Ordinary combat deaths, formal inquiries
into a death without alleged wrongdoing, and settlement-law statements expose
other policy boundaries. Do not overwrite the 5,000-row target file with Astra
labels or use these targeted-case agreement rates as accuracy estimates.

## Threshold and combination study

Thresholds were selected using only out-of-fold scores on the 4,053 older
articles. They were then evaluated on the 947 recent articles. A lower score
threshold flags more articles as adverse. Values are decision scores, **not
calibrated probabilities**. All counts below compare with silver labels.

| LSA 200 C=3 operating point | Older CV precision | Older CV recall | Recent precision | Recent recall | Recent FN | Recent FP |
|---|---:|---:|---:|---:|---:|---:|
| Default score ≥ 0 | 0.909 | 0.907 | 0.930 | 0.934 | 38 | 40 |
| Older-CV best F1, score ≥ 0.167 | 0.931 | 0.889 | 0.946 | 0.927 | 42 | 30 |
| Older-CV recall ≥ 0.95, score ≥ -0.619 | 0.806 | 0.951 | 0.874 | 0.972 | 16 | 80 |

The low threshold finds 22 more silver-positive recent articles but adds 40
false-positive flags compared with the default. On the 60 blind-reviewed recent
articles, the default LSA 200 setting had three false negatives and three false
positives versus Astra; the high-recall setting had one and seven. That small,
stratified model-review sample supports the trade-off but is not sufficient to
set a production threshold.

Simple combinations did not justify added complexity. A two-of-three vote
among the finalists gave recent F1 0.934, with 38 false negatives and 38 false
positives versus silver. Requiring all three to vote positive reduced false
positives to 27 but increased false negatives to 44. Flagging positive if any
model voted positive reduced false negatives to 34 but increased false positives
to 46. Full numbers and the selection method are in
`finalist-thresholds-v1/operating-points.csv`.

## Practical use and remaining gate

The default LSA 200 model is the preferred choice among the strong
general-purpose candidates for a **human-assisted review queue**, given the
priority on unseen publishers. If missing
adverse articles is judged substantially more costly than sending extra
articles to review, the -0.619 high-recall setting is a candidate queueing
threshold. Keep both as options until the review capacity and acceptable miss
rate are specified. Do not auto-clear articles from a previously unseen
publisher: when UK_GOV was withheld, the 200-component model missed 104 of 161
silver-positive UK articles, and the 100-component model missed 111.

To move from research to production, first resolve same-case links and freeze
case-safe training/development/test partitions. Then obtain an independent,
answer-blind human reference set focused on UK_GOV, PTV, recent news and the
seven uncertain policy cases, and use it to compare these three finalists and
choose a review threshold. The blind Astra audit reduces uncertainty about
where disagreements occur; it cannot establish true accuracy or replace that
independent reference. The current classical ML research section is complete
with this conditional recommendation and its measured limitations.

## Reproducible research artifact

The three finalists were subsequently fitted on all 5,000 proposed-label rows
and packaged together in `finalist-models-v1/finalist-bundle.joblib`, with its
data hash, configuration and artifact hash in `finalist-models-v1/manifest.json`.
`../predict_finalists.py` scores JSONL articles with the bundle and records all
three decision scores and default-threshold labels. The bundle is about 199 MB.
It is a research checkpoint for an assisted-review pilot; the earlier held-out
scores above came from separate fold and time-split fits, not this full-data fit.
