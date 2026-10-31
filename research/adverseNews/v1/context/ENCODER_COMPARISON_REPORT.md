# PDG-115: frozen pretrained encoder comparison

Completed 2026-10-29. All classification metrics use unchanged silver labels unless explicitly marked Astra.

## Result and decision

**Keep LSA 200 + RBF SVM C=3 as the primary research baseline. None of the eight tested frozen-encoder configurations improves mean publisher transfer.** The strongest fixed encoder publisher result is about seven F1 percentage points lower, and encoding is much slower on this CPU. MiniLM meanmax/RBF gives recent F1 0.936 versus 0.932 (36/37 FN/FP versus 38/40), but that small descriptive recent gain does not offset its publisher loss. This conclusion applies to these frozen approaches; it does not establish that all encoders or supervised fine-tuning would fail.

With configuration selection confined to each publisher holdout's training data, the encoder selection procedure scores mean publisher F1 **0.738**, versus **0.811** for LSA 200 C=3 (difference **-0.073**). Each publisher is unseen during both fitting and configuration selection.

The configuration selected using older group cross-validation alone is **minilm_mean_rbf_C3**. Its mean publisher-held-out F1 is **0.736**, versus **0.811** for LSA 200 / RBF SVM C=3 (difference -0.075). Its 2025–2026 F1 is **0.930**, versus **0.932** (difference -0.002).

The highest observed encoder publisher mean is **minilm_meanmax_rbf_C3** at **0.741**. This is a descriptive maximum over the eight fixed configurations, not a configuration selected independently of that holdout.

This is an exploratory representation comparison on established research holdouts. A higher score measures reproduction of the proposed labels under shift, not verified production accuracy. The six publishers are a small fixed set; article-level bootstrap uncertainty does not establish transfer to every new publisher.

## Where this fits in the product flow

Article title and full body → tokenizer → overlapping chunks → frozen encoder → article aggregation → small supervised classifier → decision score → default threshold zero → assisted-review queue. An embedding is a numeric vector representing text meaning. A frozen encoder never updates its pretrained weights. The classifier learns which vectors correspond to this adverse-news policy. No publisher, date, ID, silver-label provenance, or review answer enters the feature vector.

Every chunk is encoded. Mean pooling averages the chunk vectors; meanmax concatenates the average with the maximum value in each vector coordinate. Mean can dilute a brief adverse passage. Maximum can retain a strong local feature but can also retain noise; it does not prove a policy rule was met. For example, a late paragraph about an arrest remains in the encoded article even if the opening paragraphs only discuss routine public activity. The article classifier can still miss that paragraph after aggregation.

Linear SVM learns a straight separating boundary in vector space. RBF SVM uses a radial-basis-function kernel to allow a curved boundary. C controls the penalty for training mistakes. Both use balanced class weights calculated from training labels only and decision threshold zero. These scores are margins, not calibrated probabilities.

## Exact comparison and selection

Same 4,053 pre-2025 articles, StratifiedGroupKFold with three folds/shuffle/seed 115, 947 2025–2026 chronological holdout articles, and six older publisher holdouts. Every held-out ID and label was asserted equal to the saved LSA 200 prediction file. Explicit training and validation IDs are saved in `splits.json`. No prepared duplicate group crosses a split. Same-case links beyond those prepared groups remain unresolved.

All eight configurations were fixed before evaluation. Selection uses mean older-fold F1 only; publisher and temporal holdouts did not tune aggregation, classifier parameters or thresholds. Pretrained text embeddings were cached label-free before fitting. All 80 classifier fits see only their training rows. The global older-CV choice is independent of the recent holdout, but includes labels from publishers later withheld in publisher diagnostics. To assess the selection process itself, three inner group folds choose the configuration separately inside each outer publisher training pool (144 additional fits; 224 total). No outer publisher label enters this nested selection. Baseline held-out predictions were reused from the original separate fits. The full-data classical bundle is used solely for latency measurement.

F1 combines positive precision (how many flags are silver-positive) and recall (how many silver-positive articles are found). AP is average precision, a threshold-independent ranking measure. Publisher results average publishers equally; group results average three folds equally. Different source mixes and positive rates make group and recent F1 different tasks.

| Configuration | Group F1 | Publisher F1 | Publisher recall | Recent F1 | Recent recall | Recent FN / FP |
| --- | --- | --- | --- | --- | --- | --- |
| minilm_mean_rbf_C3 | 0.912 | 0.736 | 0.718 | 0.930 | 0.927 | 42 / 38 |
| minilm_meanmax_rbf_C3 | 0.908 | 0.741 | 0.720 | 0.936 | 0.937 | 36 / 37 |
| lsa200_rbf_C3 | 0.908 | 0.811 | 0.839 | 0.932 | 0.934 | 38 / 40 |
| e5small_meanmax_rbf_C3 | 0.907 | 0.733 | 0.707 | 0.930 | 0.927 | 42 / 38 |
| e5small_mean_rbf_C3 | 0.906 | 0.706 | 0.673 | 0.926 | 0.923 | 44 / 41 |
| minilm_mean_linear_C1 | 0.903 | 0.673 | 0.670 | 0.918 | 0.925 | 43 / 51 |
| e5small_meanmax_linear_C1 | 0.901 | 0.723 | 0.703 | 0.928 | 0.930 | 40 / 43 |
| e5small_mean_linear_C1 | 0.898 | 0.697 | 0.680 | 0.928 | 0.930 | 40 / 43 |
| minilm_meanmax_linear_C1 | 0.897 | 0.707 | 0.703 | 0.922 | 0.935 | 37 / 54 |

For the nested encoder-selection procedure, the paired source-stratified article bootstrap gives a conditional 95% interval [-0.097, -0.051] for publisher mean F1 difference (2,000 resamples, seed 115). This conditions on these publishers and silver labels. It excludes label uncertainty, same-case dependence, publisher-population uncertainty and the history of model searches.

## Unseen publishers

| Publisher | N | Positive | LSA F1 | Encoder F1 | LSA recall | Encoder recall | FN | FP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DOJ_PH | 303 | 104 | 0.855 | 0.814 | 0.904 | 0.923 | 10 → 8 | 22 → 36 |
| DTI_PH | 60 | 44 | 0.953 | 0.767 | 0.932 | 0.636 | 3 → 16 | 1 → 1 |
| NBI | 567 | 517 | 0.972 | 0.987 | 0.998 | 0.994 | 1 → 3 | 29 → 11 |
| PTV | 1293 | 284 | 0.662 | 0.694 | 0.894 | 0.873 | 30 → 36 | 229 → 183 |
| SEC_PH | 591 | 577 | 0.966 | 0.684 | 0.951 | 0.525 | 28 → 274 | 11 → 6 |
| UK_GOV | 1203 | 161 | 0.458 | 0.471 | 0.354 | 0.354 | 104 → 104 | 31 → 24 |

The preceding table describes the single global older-CV configuration. The next table evaluates the training-only nested selection procedure:

| Withheld publisher | Selected inside training data | F1 | Recall | FN / FP |
| --- | --- | --- | --- | --- |
| DOJ_PH | e5small_meanmax_rbf_C3 | 0.824 | 0.875 | 13 / 26 |
| DTI_PH | minilm_meanmax_rbf_C3 | 0.767 | 0.636 | 16 / 1 |
| NBI | minilm_mean_rbf_C3 | 0.987 | 0.994 | 3 / 11 |
| PTV | e5small_mean_rbf_C3 | 0.687 | 0.880 | 34 / 194 |
| SEC_PH | minilm_meanmax_rbf_C3 | 0.699 | 0.542 | 264 / 6 |
| UK_GOV | minilm_meanmax_rbf_C3 | 0.464 | 0.360 | 103 / 31 |

## Recent-news sources

| Source | N | Positive | LSA F1 | Encoder F1 | LSA recall | Encoder recall | FN | FP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ADB | 1 | 1 | 0.000 | 1.000 | 0.000 | 1.000 | 1 → 0 | 0 → 0 |
| AMLC | 43 | 18 | 0.750 | 0.593 | 0.667 | 0.444 | 6 → 10 | 2 → 1 |
| DOJ_PH | 188 | 40 | 0.849 | 0.827 | 0.775 | 0.775 | 9 → 9 | 2 → 4 |
| DTI_PH | 21 | 8 | 0.800 | 0.750 | 0.750 | 0.750 | 2 → 2 | 1 → 2 |
| NBI | 377 | 325 | 0.961 | 0.963 | 0.978 | 0.969 | 7 → 10 | 19 → 14 |
| PCIJ | 9 | 7 | 0.833 | 0.545 | 0.714 | 0.429 | 2 → 4 | 0 → 1 |
| PTV | 26 | 7 | 0.727 | 0.923 | 0.571 | 0.857 | 3 → 1 | 0 → 0 |
| SEC_PH | 167 | 159 | 0.965 | 0.966 | 0.956 | 0.969 | 7 → 5 | 4 → 6 |
| UK_GOV | 115 | 7 | 0.480 | 0.522 | 0.857 | 0.857 | 1 → 1 | 12 → 10 |

Rows with one or very few articles are case outcomes, not stable source-level performance estimates. All source/fold/candidate metrics are retained in `per-source.csv`, including group-fold sources.

## Strong-model diagnostic review and policy boundaries

Astra/high is a blinded second-model opinion, not human truth. The 60 recent-random articles are source-stratified; the 40 transfer-targeted articles were selected because all classical finalists disagreed with silver. Their results cannot estimate unbiased overall accuracy. No review answer was used to fit, select or tune an encoder. No corpus label was changed.

| Model | Slice | Scope | N | F1 vs Astra | Recall vs Astra | FN / FP |
| --- | --- | --- | --- | --- | --- | --- |
| encoder_selection_rule | recent_random | all | 60 | 0.879 | 0.853 | 5 / 3 |
| encoder_selection_rule | recent_random | certain_nonboundary | 59 | 0.875 | 0.848 | 5 / 3 |
| encoder_selection_rule | transfer_targeted | all | 40 | 0.474 | 0.409 | 13 / 7 |
| encoder_selection_rule | transfer_targeted | certain_nonboundary | 32 | 0.438 | 0.368 | 12 / 6 |
| lsa200_rbf_C3 | recent_random | all | 60 | 0.912 | 0.912 | 3 / 3 |
| lsa200_rbf_C3 | recent_random | certain_nonboundary | 59 | 0.909 | 0.909 | 3 / 3 |
| lsa200_rbf_C3 | transfer_targeted | all | 40 | 0.488 | 0.455 | 12 / 9 |
| lsa200_rbf_C3 | transfer_targeted | certain_nonboundary | 32 | 0.424 | 0.368 | 12 / 7 |
| minilm_mean_rbf_C3 | recent_random | all | 60 | 0.879 | 0.853 | 5 / 3 |
| minilm_mean_rbf_C3 | recent_random | certain_nonboundary | 59 | 0.875 | 0.848 | 5 / 3 |
| minilm_mean_rbf_C3 | transfer_targeted | all | 40 | 0.474 | 0.409 | 13 / 7 |
| minilm_mean_rbf_C3 | transfer_targeted | certain_nonboundary | 32 | 0.452 | 0.368 | 12 / 5 |

encoder_selection_rule uses nested publisher selection for transfer cases and the older-CV choice for recent cases. Candidate-level review scores remain separately available.

`policy-boundary-cases.csv` isolates the nine named boundary cases and all uncertain judgments. The certain_nonboundary rows exclude their union; they still reflect another model's judgments. Earlier user exclusions for cyber libel (R1412) and preliminary Show Cause Orders (R0431) take precedence over Astra's positive calls. Ordinary combat, settlement illegality, and investigations of deaths without stated wrongdoing remain separate policy decisions. See `../POLICY_BOUNDARY_REVIEW.md`.

## Full-text coverage, CPU cost and memory

| Encoder | Articles / chunks | Multi-chunk articles | Longest chunks | First-window token coverage | Encoding seconds | Articles/sec | OS peak memory MiB | Model directory MiB | Chunk cache MiB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| minilm | 5000 / 16921 | 4443 | 58 | 36.4% | 638.8 | 7.83 | 487 | 86.7 | 25.1 |
| e5small | 5000 / 10049 | 2939 | 31 | 58.6% | 1492.9 | 3.35 | 1444 | 465.3 | 15.7 |

First-window coverage counts unique content tokens, before overlap. Every token was covered by the actual chunked run. Both models use 384-dimensional chunk vectors; meanmax has 768 article dimensions. The tokenizer counts differ by model, so coverage percentages are model-specific.

| Stage | Chunks under selected encoder | Model | N | F1 | Recall | FN / FP |
| --- | --- | --- | --- | --- | --- | --- |
| cv | one | lsa200_rbf_C3 | 470 | 0.724 | 0.702 | 34 / 27 |
| cv | one | minilm_mean_rbf_C3 | 470 | 0.753 | 0.711 | 33 / 20 |
| cv | two_to_four | lsa200_rbf_C3 | 2860 | 0.910 | 0.919 | 94 / 115 |
| cv | two_to_four | minilm_mean_rbf_C3 | 2860 | 0.918 | 0.912 | 102 / 86 |
| cv | five_plus | lsa200_rbf_C3 | 723 | 0.948 | 0.928 | 31 / 13 |
| cv | five_plus | minilm_mean_rbf_C3 | 723 | 0.935 | 0.905 | 41 / 13 |
| publisher | one | lsa200_rbf_C3 | 470 | 0.628 | 0.658 | 39 / 50 |
| publisher | one | minilm_mean_rbf_C3 | 470 | 0.658 | 0.640 | 41 / 35 |
| publisher | two_to_four | lsa200_rbf_C3 | 2854 | 0.852 | 0.908 | 106 / 258 |
| publisher | two_to_four | minilm_mean_rbf_C3 | 2854 | 0.814 | 0.813 | 216 / 214 |
| publisher | five_plus | lsa200_rbf_C3 | 693 | 0.944 | 0.926 | 31 / 15 |
| publisher | five_plus | minilm_mean_rbf_C3 | 693 | 0.704 | 0.559 | 184 / 12 |
| temporal | one | lsa200_rbf_C3 | 87 | 0.619 | 0.565 | 10 / 6 |
| temporal | one | minilm_mean_rbf_C3 | 87 | 0.732 | 0.652 | 8 / 3 |
| temporal | two_to_four | lsa200_rbf_C3 | 744 | 0.952 | 0.962 | 18 / 28 |
| temporal | two_to_four | minilm_mean_rbf_C3 | 744 | 0.944 | 0.952 | 23 / 31 |
| temporal | five_plus | lsa200_rbf_C3 | 116 | 0.889 | 0.865 | 10 / 6 |
| temporal | five_plus | minilm_mean_rbf_C3 | 116 | 0.894 | 0.851 | 11 / 4 |

Length slices are pooled article metrics, not publisher-macro metrics, and their class/source mixes differ. They show residual misses after complete token coverage; coverage alone does not guarantee policy recognition.

Encoding wall time includes tokenization, inference, article aggregation, cache reads/writes and progress updates, but excludes model load/download and final feature-file writing. Forty pilot articles per encoder were already cached; the full run computes the remaining articles. Pilot timings are saved separately. E5 pilot briefly overlapped MiniLM encoding; full model runs were sequential. Peak memory is the Windows process high-water working set, not total system demand. A source-length mix can change throughput substantially.

The existing LSA bundle took **20.9s** to transform and score the same 5,000 full articles in batches of 100 (**238.92 articles/s**), plus **1.8s** to load the three-finalist bundle. Its loaded bundle includes other classical finalists; its size/memory is not a minimal LSA-only package. This is a local throughput comparison with different natural batching, not a controlled online p95 latency benchmark.

| Configuration | Classifier fitting, all 10 splits (s) | Classifier scoring, all 10 splits (s) |
| --- | --- | --- |
| minilm_mean_rbf_C3 | 4.046 | 1.233 |
| minilm_meanmax_rbf_C3 | 11.749 | 2.785 |
| e5small_meanmax_rbf_C3 | 10.452 | 1.922 |
| e5small_mean_rbf_C3 | 3.274 | 0.971 |
| minilm_mean_linear_C1 | 0.983 | 0.015 |
| e5small_meanmax_linear_C1 | 1.931 | 0.030 |
| e5small_mean_linear_C1 | 0.859 | 0.018 |
| minilm_meanmax_linear_C1 | 2.151 | 0.026 |

Classifier-only costs exclude encoder extraction. Baseline historical full feature fitting was shared across three SVM variants and included other feature families, so it is not presented as an isolated LSA training benchmark. Nested selection costs are saved separately in `nested-inner-metrics.csv`. No paid inference/API calls were used; no dollar cost is inferred from CPU time.

## Error investigation and reproducibility

`false-negatives.csv` lists every silver-positive miss for all models and splits. `selected-paired-disagreements.csv` distinguishes encoder rescues from encoder regressions on the same articles. `review-cases.csv` preserves separate silver, Astra, uncertainty and prediction columns. Read these outcomes as evidence for review routing and training coverage, not automatic relabeling.

### What the errors show

The global older-CV MiniLM choice misses **274 of 577** silver-positive SEC articles when SEC is withheld; LSA misses **28**. Nested selection chooses meanmax for that source and still misses **264**. It misses **16 of 44** DTI positives versus **3** for LSA. On UK_GOV, the global choice misses **104 of 161**, exactly the LSA count; nested selection misses **103**. PTV is a trade-off: the global choice reduces false positives from **229 to 183**, but increases misses from **30 to 36**. A broad semantic representation therefore did not translate into broader policy transfer. The experiment does not isolate whether the limiting cause is pretraining, aggregation, training coverage or source/label patterns; complete token coverage rules out silent first-window truncation as an explanation for this run.

| Case | Observed behavior and interpretation |
| --- | --- |
| AN-P115-R3082, SEC | NEW WORLD AI advisory: unauthorized solicitation and a possible Ponzi scheme are explicit in the full text. LSA flags it; global MiniLM clears it despite encoding all six chunks. This is a concrete policy-relevant miss relative to the supplied text and silver label. |
| AN-P115-X287, DTI | Uncertified fireworks were actually seized. LSA flags it; MiniLM clears it despite all three chunks. The seizure distinguishes this from an excluded preliminary request for explanation alone. |
| AN-P115-R2255, PTV | Silver-positive, LSA-positive, MiniLM-negative. Full text reports general crime trends and prevention-policy claims without a concrete qualifying incident. This reading suggests a label-audit candidate; it is not evidence that every silver-relative encoder miss is a real policy miss. No label was changed. |
| AN-P115-R0001, ADB | MiniLM rescues the recent debarment/procurement-fraud case that LSA misses; both silver and Astra are positive. This is useful case-level improvement despite the aggregate regression. |
| AN-P115-R4395, UK | MiniLM predicts negative and agrees with silver; Astra identifies a specific hospital attack and predicts positive, while LSA predicts positive. A wrongly negative silver label can hide a miss from the ordinary false-negatives file. This remains a separate review observation, not human adjudication. |

The first three examples were selected after scoring to inspect disagreements, with full supplied bodies saved in `regression-case-extracts.json`; they are illustrative, not a random error-rate sample. The last two come from the existing blinded review. Transformer semantics can help particular cases while losing enforcement details in others. Meanmax did not remove the SEC regression, so simply changing from mean to maximum-inclusive pooling was insufficient here.

The 5,000-row corpus matches the earlier finalist data hash. Fifteen corpus/audit files have identical before/after SHA-256 hashes. Reserved data was never opened or used. Pinned model revisions, file hashes, cache contracts, package versions, resource snapshots and exact split IDs are saved alongside results.

Run `run_encoder_screen.py download --model minilm`, then `pilot`, then `encode`; repeat for `e5small`; run `evaluate`, `benchmark-baseline`, and `summarize_encoder_screen.py`. Use `--data-root` and `--output` for explicit paths when running from the research checkout. Install `requirements-encoder.txt` in the isolated research environment. See `DESIGN.md` for official model-card links, licenses, selection rationale and exact representation definitions.

Fine-tuning was not attempted. There is no confirmed CUDA GPU, available RAM is limited, and frozen-feature evidence should be assessed before increasing training cost. A separate, case-safe benchmark and independent reference would be needed to justify a training experiment and establish production performance. Given the observed publisher regression and 30–71x higher extraction time in this local throughput comparison, extra encoder training was not justified in this stage. The next useful evidence is an answer-blind reference and targeted SEC/DTI/UK label/source review, with policy boundaries resolved separately. No unit tests were added or run under the project workflow.

Three takeaways: (1) better older-fold F1 did not imply better unseen-publisher transfer; (2) full-text coverage is necessary but does not guarantee the policy is recognized; (3) LSA remains the stronger, faster research option, while label uncertainty and weak UK recall remain unresolved by either representation.
