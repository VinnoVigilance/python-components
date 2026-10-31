# PDG-115: adding source depth and handling unfamiliar publishers

## Plain answer

Keep the fast LSA 200 + RBF SVM C=3 classifier as the research baseline. We added 181 certain stronger-model opinions to an experimental copy of the training corpus; the updated model did **not** improve agreement on the reserved packet. This result argues against collecting a large volume of similar UK_GOV and PTV examples indiscriminately. Do not use a rule such as “30 publishers makes the model robust”: the current corpus has nine publishers (ten named datasets), and the available experiment cannot estimate a safe minimum number.

For an unfamiliar publisher, send every article to human review during the pilot. The model score can order that queue, but cannot clear an article automatically. This is a conservative routing recommendation, not a production deployment or an accuracy claim.

### Why the fast classical model currently wins

The earlier controlled feature comparison held whole publishers out of training. Its LSA 200 / RBF SVM C=3 baseline reached mean publisher F1 **0.811** against silver labels. The strongest of eight frozen-encoder configurations reached **0.741** when viewed descriptively; a configuration selected inside each training split reached **0.738**. A *frozen encoder* is a pretrained text model whose weights do not change for this task. *LSA* compresses word and character patterns into 200 numeric features; the SVM learns a boundary from those features.

The reason for the measured gap is not proven, but the task gives a plausible explanation: short exact phrases about charges, sanctions, investigations, or denials matter to the labeling policy, and the word/character representation keeps more of their surface form. A general-purpose encoder can make differently worded articles look semantically similar even when a short policy-critical passage changes the label. Conversely, it can help on other slices: the encoder comparison found small gains for some short articles, so this is not a universal rule about all neural models.

The local batch throughput measurement was about **239 articles/second** to transform and score with the existing LSA bundle, versus **7.8** for MiniLM and **3.35** for E5-small during their full encoding passes. Those runs used different batching and stages, so they establish the rough CPU cost in this workspace, not a production latency service-level guarantee. See the encoder report for memory, full-text chunk coverage, and all source-level results.

## Where this fits in the product flow

New article → identify publisher → score its title and body → choose a review queue → collect reviewer decision → record errors and eventually update training data. The score is an SVM *margin*: distance from a learned decision boundary, not the probability that an article is adverse. A positive margin sends an article toward positive review. A negative one does not prove it is safe.

The learning loop has two separate jobs:

1. **Training examples** teach the classifier. We selected 100 new UK_GOV and 100 new PTV articles from material outside the current 5,000 examples. Within each publisher, 25 have high existing-model scores, 25 low scores, 25 scores near zero, and 25 were chosen at random. This mix finds possible misses and confusing articles while keeping a less targeted slice.
2. **A reserved diagnostic packet** measures how the model and a stronger reviewer disagree. We selected 150 articles by a fixed random hash before looking at their scores: UK_GOV 30, PTV 30, NBI 20, SEC_PH 20, DOJ_PH 20, DTI_PH 10, AMLC 10, PCIJ 10. These articles must never enter the new training set. A second model's answers remain *model opinions*, not independent human truth.

The packets are in `training-blind.jsonl` and `reference-blind.jsonl`. Selection details and hashes are in `manifest.json` and the internal CSV files. Selection excluded exact prepared groups in the existing 5,000. A title-similarity scan flagged 25 of 350 articles for possible links to older articles. This scan is only a heuristic; case-level separation has not been verified.

## What the existing labels show

All numbers below compare predictions to *silver labels*: labels previously proposed by a model. F1 combines precision (the share of flagged items that are positive) and recall (the share of positives that were flagged). A false negative is a positive article the model missed. These results describe agreement with the silver labels under these particular splits, not verified production accuracy.

### More articles from a known publisher

For each source, train on older articles and evaluate later articles from that same source. Other-source training articles stay fixed while the number of target-source articles grows. The cutoff is 2019-01-01 for PTV and 2020-01-01 for UK_GOV. Each source has one deterministic hash-based ordering, so a single learning curve cannot establish a reliable trend.

| Publisher | Older publisher articles added | Later articles | F1 | Recall | False negatives | False positives |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PTV | 212 | 471 | 0.721 | 0.805 | 15 | 33 |
| PTV | 424 | 471 | 0.759 | 0.779 | 17 | 21 |
| PTV | 848 | 471 | 0.728 | 0.714 | 22 | 19 |
| UK_GOV | 213 | 466 | 0.554 | 0.554 | 25 | 25 |
| UK_GOV | 426 | 466 | 0.557 | 0.571 | 24 | 27 |
| UK_GOV | 852 | 466 | 0.574 | 0.625 | 21 | 31 |

UK_GOV gets a modest F1 and recall gain from more same-source examples. PTV improves at the middle size, then loses recall at the largest size. The PTV result is a concrete reason to select informative, policy-correct examples and remeasure, instead of assuming that an indiscriminate data increase will fix the problem. The larger training subsets also change their positive-label mix, so article count is not the only possible cause.

### More publisher variety

With 900 older training articles fixed, we held out each of five sufficiently large publishers and trained either on three of the remaining publishers (300 articles each) or four (225 each). Mean F1 across the five held-out publishers was **0.743** with three training publishers and **0.767** with four. The fourth publisher helped on average, but hurt the SEC_PH result; the exact mix mattered. On held-out UK_GOV, three-source combinations ranged from about 0.333 to 0.539 F1, while the four-source result was 0.517. On held-out PTV, the corresponding range was about 0.426 to 0.589, with four sources at 0.570.

This is evidence that different publishers bring different writing styles and types of cases. It is **not** evidence that 30 publishers is sufficient, necessary, or a sensible target. There are only five sizeable sources in this experiment. Changing source count also changes which articles and class proportions enter training. To establish robust deployment, we need repeated holdouts on several genuinely new publishers and human-reviewed labels, not a round publisher count.

### Queue cost for weak sources

The existing publisher-holdout predictions demonstrate why a single permissive score threshold cannot safely clear unfamiliar articles. At the normal threshold of zero, UK_GOV put 88/1,203 articles into the positive queue and missed 104/161 silver positives. Lowering the queue threshold to -0.619 put 212 into the queue but still missed 58/161. For PTV the corresponding counts changed from 483 queued and 30/284 missed to 818 queued and 7/284 missed. The lower threshold raises review workload substantially and still misses some labeled positives.

The `triage_new_publishers.py` research command uses source identity and score to route articles. New publishers and the two weak sources go to full review; all output rows explicitly say `auto_clear=false`. The command is a pilot prototype, not connected to production.

## Stronger-model review and data update

The user chose GPT-6 Astra with medium reasoning effort to review the 200 training articles and 150 reserved articles. This is a stronger-model review relative to the earlier Luna labels, while using less reasoning effort than an Astra-high pass. Its actual cost and accuracy are not measured here. Source, model score, and previous label are hidden from the review packets. The review records an exact evidence excerpt for a positive label, a short policy reason, and an uncertainty flag.

The stronger model completed all **350 opinions**. The 200 training articles yielded 85 positive opinions and 19 uncertain cases. Of the 181 certain cases, 76 were positive and 105 negative. The original fast classifier already agreed with 165/181: it missed six teacher-positive cases and flagged ten teacher-negative cases. Thus most new examples repeated patterns the fast model already handled. This is a plausible explanation for the small effect of retraining, not proof that no better data selection exists.

`returns/` contains the answers. In the first reserved batch, 20 negative answers had stray evidence text. We saved the original file as `astra-medium-reference-original.jsonl` and cleared only the evidence field for those negatives; labels and reasons were preserved. The 150 reserved opinions contain 73 positives and 18 uncertain cases. `review-queue.csv` contains the 86 articles with at least one of three review signals: uncertainty, fast/stronger model disagreement, or a possible link to an existing case. Those signals overlap. This is a queue for human policy and case review, not a validated reference set.

Only certain, schema-valid training answers entered an **experimental** augmented corpus of **5,181** articles; the original 5,000 stayed intact. Uncertain cases were excluded. Reserved answers stayed out of training. `import_source_stage_labels.py` enforces those boundaries.

### Did the teacher's new examples help the fast student?

We refit the same fast model design with the 5,181-row experimental corpus. On the 150 reserved articles, it changed just **one** prediction: a UK_GOV appointment announcement moved from negative to positive while Astra-medium labeled it negative. Overall F1 against Astra-medium moved from **0.881 to 0.875**; recall stayed **0.863**, and both versions missed **10 of 73** Astra-positive articles. On the 132 reserved cases Astra marked certain, F1 moved from **0.923 to 0.916**, with the same eight missed positives. These are model-agreement scores, not accuracy against people.

| Reserved source | Articles | Astra-positive | Original F1 | Updated F1 | Original missed | Updated missed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| UK_GOV | 30 | 5 | 0.571 | 0.500 | 3 | 3 |
| PTV | 30 | 9 | 0.824 | 0.824 | 2 | 2 |
| All eight sources | 150 | 73 | 0.881 | 0.875 | 10 | 10 |

UK_GOV missed examples include an attack mentioned inside a diplomatic call, a past terrorist bomb plot mentioned inside a visa-policy announcement, and a past Daesh invasion mentioned inside an army-support statement. Their titles look like routine government news. Under the written article-level policy, a concrete qualifying matter in the body can make the entire article positive even if it is not the headline. These examples suggest a failure pattern worth targeting, but three articles cannot establish its frequency. The near-identical before/after predictions show this batch did not teach the model that pattern.

Teacher–student training means using a slower, stronger model's answers to train the fast classifier. This was a small version of that idea: Astra-medium proposed labels, then we refit the fast classifier. The reserved comparison found no gain. A sensible next batch would focus on verified missed-positive patterns, especially adverse details buried in otherwise routine UK_GOV text, and on the 37 uncertain policy boundaries. A large teacher batch or large-model fine-tuning would consume more review and compute before we know whether its labels fix the actual misses.

## Decision gates before production use

1. Review the 37 uncertain opinions, 43 fast/stronger-model disagreements, and 25 possible same-case links in `review-queue.csv`. Several signals overlap. Keep a record of the policy reason behind each accepted training label. The 25 title-similarity flags are mostly NBI articles with the same generic headline; that is not proof they concern the same case.
2. Collect a smaller follow-up batch designed around confirmed missed-positive patterns and new publishers. Keep its evaluation sample reserved before choosing examples to train on. Do not promote the 5,181-row experiment: its reserved model-agreement result is slightly worse than the baseline.
3. Have independent people label a fresh, case-separated, source-stratified sample from actual production arrivals, including genuinely new publishers. That is the gate for claims about production recall, false negatives, and automatic clearance. Model agreement alone cannot pass it.
4. During a pilot, route all unknown publishers to review, track misses and review load by publisher, then relax routing only after the human-reviewed evidence supports it.

## Concrete example

A new site reports that a company was prosecuted for illegal waste disposal. The fast model gives a negative score because the site's language differs from training publishers. The publisher is unknown, so the article still enters full review. If a reviewer confirms the prosecution and quotes the sentence that proves it, the article becomes a candidate training example. A later article about the same case should not appear in both training and evaluation, or the evaluation would look easier than genuinely new news.

## Takeaways to explain to a senior engineer

- More UK_GOV training material is worth trying only when it adds verified examples of missed patterns. The 181-example teacher pilot changed one reserved prediction and did not improve model agreement.
- Publisher diversity matters, but there is no evidence for a minimum of 30 publishers. Source mix, label quality, and genuinely new-publisher evaluation matter more than a target count.
- A stronger model can cheaply propose labels for the fast student, but production accuracy still requires independent human-labeled, case-separated news. Until then, unfamiliar publishers need full review.

## Evidence files

- `../source-depth-v1/metrics.csv` and `manifest.json`: later same-source learning curves.
- `../source-diversity-v1/metrics.csv` and `summary.json`: fixed-size held-publisher experiment.
- `../source-triage-v1/publisher-queue-tradeoffs.csv`: review workload at two thresholds.
- `case-link-review.csv` and `case-link-audit.json`: possible same-case links, not adjudicated.
- `model-opinion-comparison/metrics.csv`: frozen baseline versus Astra-medium opinions on targeted and reserved packets.
- `augmented-corpus-astra-medium/manifest.json` and `teacher-student-pilot/metrics.csv`: accepted training opinions and before/after model-agreement results.
- `review-queue.csv`: policy, disagreement, and possible case-link review candidates.
- `../encoder-screen-v1/ENCODER_COMPARISON_REPORT.md`: earlier frozen-encoder comparison against the fast baseline.
