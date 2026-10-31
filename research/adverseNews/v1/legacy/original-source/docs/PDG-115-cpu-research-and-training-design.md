# PDG-115 — CPU-only research, data, training and evaluation design

Date: 2026-10-28. Status: proposed experiment plan; no training, inference, downloads of model weights, benchmarks or tests executed.

## 1. Decision in plain language

Use a **data-first, CPU-first** research pipeline. Begin with human-checked binary examples, train classical baselines, compare frozen semantic embeddings, and then decide whether local LLM labeling and a trained small encoder improve the result enough to justify their cost.

Do not make a large language model a mandatory production dependency or assume a teacher is automatically more accurate. Our available hardware is approximately 12 CPU cores and 32 GB RAM, with no GPU. That favors sparse text classifiers and compact encoders. A quantized local LLM is feasible to investigate as a limited offline labeler/challenger, but processing thousands of full articles may take substantial time. Measure before scaling.

Recommended experiment order:

```text
Usable articles + frozen adverse policy
  -> duplicate/case grouping and independent split reservations
  -> small human reference/pilot
  -> TF-IDF + logistic regression / linear SVM, plus NB-SVM baseline
  -> frozen small-encoder features + classifier
  -> local quantized Qwen pilot, measured against human labels
  -> targeted annotation/review and dataset expansion
  -> optional small-encoder training / teacher-to-student experiment
  -> choose cheapest adequate model on development data
  -> one final frozen evaluation, then independent shadow deployment
```

The training target and final business decision are strictly `0` or `1`. Unusable article data is filtered before labeling. Pending annotation, processing errors and audit metadata are workflow information, not extra classes.

## 2. What is established, and what is not

### User-confirmed constraints

- The definition is broadly accepted; article adversity differs from victim/perpetrator involvement.
- No budget for paid API labeling or inference.
- Hardware is CPU-only, approximately 12 cores and 32 GB RAM.
- The previous approximately 5,000-article risk dataset is not currently available. Its targets were risk categories, not adverse `0/1`.
- Database article samples will be supplied later. Some canonical article examples currently exist in local final JSONL files.
- Adverse assessment has its own pipeline and must remain independent of risk-category predictions.

The earlier guide available at `C:/Users/admin/Downloads/PDG-115-adverse-news-labeling-guide.md` records the binary policy and proposed prompt. It treats victim-centered crime reporting as adverse and resolution-only reporting as negative with retained status-update context. Confirm remaining policy exceptions in the pilot rather than silently changing them during modeling.

### Repository evidence inspected

| Evidence | What it establishes |
| --- | --- |
| `intelligence/risk/mediaRiskCategory/models/nbsvm-v1/run.json` | Existing media-risk artifact is NB-SVM with 11 topic labels; recorded training rows 3,396 and validation rows 728. Its quality is not evidence for adverse detection. |
| `intelligence/risk/watchlistRiskCategory/README.md` and deployed manifest | Watchlist uses a small text student plus lookup rules and a victim gate. This runtime is not a ready-made article-adversity model. |
| Neighboring checkout `D:/cloned repos/python-components - Copy/research/watchlistRiskClassification/` | Historical research describes local Qwen teachers, quantization, held-out source/person splits and an e5-small student trained on teacher scores. It is a historical reference, not the unavailable media corpus. |
| That checkout's `scripts/checker.py`, `scripts/student.py` | Teacher uses relative yes/no token scores. Student uses binary cross-entropy with soft teacher targets. Stored scores are not independently established probabilities of correctness. |
| That checkout's `notebooks/04_checker_explained.md` | Explicitly warns that generated explanations sometimes invented facts or contradicted scores, and that some reference review was performed by another LLM rather than a human. |

The useful inheritance is the experimental discipline and offline-teacher/CPU-student pattern. Do not inherit source lookup labels, victim suppression, passage-positive aggregation, prompts or weights as the new ground truth. Historical GPU timings do not predict CPU full-article throughput.

Earlier PDG-115 working Markdown files are absent from this checkout at inspection time; they were not recreated or treated as proof of an implemented processor. A separate research plan is created here. Existing implementation changes and merge-conflicted files are outside this task.

## 3. Keep three pipelines distinct

### Data and annotation pipeline — offline

```text
Database export / canonical article files / audited external candidates
 -> input-quality checks, language/source/time inventory
 -> content hashes, exact/near-duplicate and case clusters
 -> fixed split assignments
 -> human seed labels and optional local-teacher suggestions
 -> adjudication and versioned accepted binary labels
 -> reproducible training/development/evaluation exports
```

**Adjudication** means resolving conflicting judgments into an accepted decision while preserving the original judgments. **Gold** means independently reviewed reference labels; **silver** means machine-generated labels not individually verified to that standard.

### Research and training pipeline — offline

```text
Frozen data snapshot + split manifest + policy/prompt versions
 -> train candidate text representations and classifiers
 -> compare on development data; inspect errors
 -> select training recipe and decision threshold
 -> freeze candidate and evaluate once on final reference data
 -> package weights/features/tokenizer, input recipe and manifest
```

### Production assessment pipeline — independent post-processing

```text
core.media_record: exact canonical title/body version
 -> input eligibility
 -> selected packaged local classifier
 -> integer adverse label 0/1 + versioned assessment metadata
 -> independent adverse-assessment storage and consumer publication
```

Risk classification can run alongside it. No risk-model result is required for labeling, training features, or production inference. The teacher is normally absent from production. Missing/unusable text receives no label and remains visible in processing diagnostics.

Source updates, deletion, retries, initial backfill, calculation-version changes and downstream freshness still need explicit operational handling in the separate assessment pipeline. Do not mutate canonical article content to store model output. Database-table implementation is a later phase, not required to decide the research winner.

## 4. Data acquisition: use our articles first

Request a database export containing article ID, physical source version, logical identity if available, source/dataset, URL, publication and ingestion dates, title, full canonical body, language if known, and extraction-quality information. Text content is the model input; the other fields support audit and splitting.

Do not require risk-category columns. Existing category labels may remain in a different dataset, but they must not define the new label or enter semantic features.

Collect routine news as well as likely adverse events from the configured sources. Preserve neutral government announcements, prevention guidance, administrative notices and resolution updates as real negatives. Otherwise a classifier learns to flag every regulator/police article.

Start with a broad source/date export, preferably several thousand to tens of thousands of usable candidates if the database can supply them. This is a candidate pool, not a requirement to pay for or fully label every row. More duplicated text is not more independent evidence.

Filter empty bodies, error pages, navigation-only content and genuinely unusable extraction. Do not discard a short but complete official notice. Missing title is acceptable. Unsupported-language records should be routed to an appropriate annotation workflow, not mislabeled `0`. Do not assume English from a missing language field. Whether English-only is a sensible first release depends on the export's actual language mix; bilingual Persian/English policy documentation does not itself define supported news languages.

### Public data and benchmarks actually found

| Candidate | Finding | Proposed use |
| --- | --- | --- |
| [UT adverse-media project](https://github.com/kristjanr/ut-ml-adverse-media) | Authors report 977 adverse and 642 non-adverse articles with cross-checking; training archives are listed. Repository has an MIT software license. Article licensing, label definitions, duplicates and text quality still need auditing. | Promising additional candidate corpus; independently check/remap labels to our policy. Do not treat its reported scores as VV performance. |
| [Webz.io/Webhose repository](https://github.com/Webhose/adverse-media-dataset) | Collection query selects English negative-sentiment business/finance news. | Candidate text only, not ready-made adverse gold. Check intended-use terms. |
| [rangeva Hugging Face listing](https://huggingface.co/datasets/rangeva/adverse-media-dataset) | Viewer states the dataset is empty and card redirects to Webz.io; the large size tag does not establish downloadable labeled examples. | Do not count as a separate available corpus. |
| [KYBench](https://labs.taktile.com/benchmarks/kybench) | Company-level adverse-media search/investigation benchmark, not a supplied single-article binary training contract. | Future end-to-end product research; not a direct replacement for our article dataset. |
| [Operational-risk research](https://aclanthology.org/2020.acl-main.78/) | Paper describes financial-news operational-risk classification; its described dataset is proprietary and targets differ. | Method reference, not an established available binary corpus. |
| [FinEntity](https://aclanthology.org/2023.emnlp-main.956/) / financial sentiment data | Entity-targeted sentiment is a different task. | Potential confusing-text examples only; negative sentiment is not label `1`. |

This search found a useful binary-data lead, not a ready-made licensed production benchmark matching all of our decisions. Raw public archives were not downloaded, counted or inspected record by record. A software license alone does not settle rights in scraped article text. Confirm usable data terms before incorporating a corpus; do not reproduce commercial content unnecessarily.

## 5. How many adverse articles do we need?

There is no defensible universal answer such as “5,000 is enough.” We need **independent positive and negative examples**, coverage of the difficult boundaries, and a separate evaluation pool. The following are planning targets, not measured requirements or accuracy guarantees.

### Recommended staged counts

| Stage | Data target | Purpose |
| --- | --- | --- |
| Pilot | 300–400 usable articles: approximately 200 randomly sampled, plus 100–200 deliberately varied boundary cases | Estimate class prevalence in the random subset, discover policy disagreement and benchmark initial teacher quality. Double-label a substantial subset independently and adjudicate disagreements. |
| First supervised run | Around 1,000 training articles, aiming for roughly 300–500 adverse and the rest negative where feasible | Establish baseline behavior before expensive labeling or training. Pilot examples may become training data after policy stabilization. |
| Development pool | Around 400–600 separately labeled articles; preserve a representative component and identify enriched cases explicitly | Select candidate, hyperparameters and threshold; refine prompt only here or in the pilot. Do not report enriched precision as production precision. |
| Expansion target if needed | Around 5,000 training articles total, including roughly 1,500–2,500 adverse articles and the remaining negative articles | Broaden sources, dates, languages and failure types. This is a target to evaluate with learning curves, not a mandate to fill a fixed quota. |
| Final representative evaluation | Start with 1,000–1,500 fresh, independently labeled articles in natural source/time proportions, reserved before training | Estimate production-like precision, recall and false omissions. If positives are rare, this may be insufficient for a strong recall claim. |
| Separate challenge/positive coverage | Around 200–300 independent adverse cases and 150–250 hard negatives if the representative pool lacks them | Measure policy boundaries and rare cases. Preserve separately; do not merge into an unweighted production precision estimate. |

These pools are separate except where pilot examples explicitly become training examples. Final evaluation cases cannot become development examples while still being called untouched. An adverse case can generate multiple copied articles; count the case groups as well as articles.

**Our starting decision:** target approximately **5,000 training articles with about 2,000 adverse and 3,000 non-adverse**, but run the first comparison at approximately 1,000. Stop earlier if quality and coverage are adequate; acquire targeted examples rather than mechanically multiplying data when errors are systematic.

### Why negative examples matter as much as positive ones

The classifier must distinguish a fraud allegation from fraud-prevention guidance, a live investigation from acquittal-only reporting, ransomware from a power failure, and misconduct from ordinary losses. Easy unrelated news alone does not teach these boundaries. Conversely, do not construct all negatives from one source and positives from another: that would teach source style.

Positive coverage should span event types and stages, named/unnamed actors, victim-centered reporting, historical reporting, multiple actors, and long/short text. Negative coverage should span routine notices, education, ordinary commercial difficulty, hypothetical content, status-only mentions, civil-only disputes and resolution-only updates. These are sampling/audit attributes, not extra model classes or required risk-category annotations.

### Candidate-pool size depends on prevalence

**Prevalence** is the fraction of usable incoming articles that are adverse. To obtain about 2,000 positives by random collection alone:

| Actual adverse prevalence | Approximate usable articles to inspect/label |
| --- | ---: |
| 10% | 20,000 |
| 30% | 6,700 |
| 50% | 4,000 |

These are arithmetic illustrations, not observed source rates. Estimate prevalence from the pilot's random subset. Targeted collection can enrich training positives, but maintain natural-distribution evaluation and log the selection method. Two thousand repeated arrest announcements may add less value than a few hundred varied, correctly labeled boundary cases.

### If staff time is limited

No paid API does not mean zero annotation cost: staff attention, CPU time and electricity remain. Prioritize a small human reference and final evaluation over pretending thousands of automatic labels are gold. If only 100–200 reference articles are possible initially, call results exploratory and show the class counts and wide statistical uncertainty. Do not claim production readiness from that budget.

## 6. CPU-only model ladder and finite experiment matrix

An **encoder** converts text into numerical features. A **frozen encoder** uses pre-existing weights unchanged. **Fine-tuning** adjusts those weights for our task. **Distillation** trains a smaller model using a teacher's outputs. None requires training a foundation model from scratch.

| ID | Experiment | Training labels | CPU role / decision |
| --- | --- | --- | --- |
| E0 | Majority-class prediction and a small keyword/rule baseline | None / pilot policy | Sanity reference only; not a keyword filter before learning. |
| E1 | Word + character TF-IDF with logistic regression and linear SVM; compare binary NB-SVM | Accepted human/silver `0/1` as specified per run | Main low-cost production candidates. Sparse TF-IDF weights words or short sequences by occurrence and distinctiveness. Character features can help spelling variation. |
| E2 | Frozen `intfloat/multilingual-e5-small` embeddings + logistic regression; optional TF-IDF/embedding combination | Same labels and splits as E1 | Main semantic CPU challenger. Cache features once so classifier comparisons are cheap. |
| E3 | One small encoder trained/fine-tuned for article-level binary classification; SetFit-style experiment only where its document representation is valid | Human first; then controlled silver-data addition | Conditional escalation if E2 fails important contextual boundaries. Time a small CPU pilot before committing to a sweep. |
| E4 | Local Qwen3-1.7B and Qwen3-4B-Instruct-2507, quantized, prompted with frozen policy | No parameter training; human reference evaluates them | Offline teacher/challenger comparison. Start with 50–100 dev articles before bulk use. |
| E5 | Refit E1/E2 and optionally E3 with selected teacher-labeled training data and human corrections | Binary hard targets; optional teacher-score experiment only as separate training metadata | Tests whether a local teacher actually improves downstream quality. |
| E6 | Fine-tune an open generative LLM with LoRA/QLoRA | Curated binary instruction examples | Deferred on current hardware. Consider only with strong evidence and different compute/time availability. |

Choose models for controlled comparison rather than testing every published family. Qwen is a practical starting point because the historical workflow already used it, not because its size guarantees superiority. Selected model cards list Apache-2.0 for [Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) and [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507). Record exact weight revisions and any quantized conversion provenance. These are named candidates, not claims about the newest/best model on the market.

**Suggested first production contenders: E1 and E2.** E3/E5 earn their place through better development performance. A local teacher that loses on the definition must not label the whole corpus merely because it is an LLM.

### Why classical ML can still win

Full-document sparse features see text without a short encoder's token truncation, train economically, and can be strong on a well-defined binary task. Contextual models may better distinguish relationships and negation but may lose information or overfit a small corpus. Semantics is a capability to measure on examples, not a reason to skip baselines. NB-SVM is supported as a useful classical comparison by [Baselines and Bigrams](https://aclanthology.org/P12-2018/); its sentiment/topic findings do not prove adverse-task performance.

## 7. Local teacher design without a GPU or paid API

Use a CPU-compatible runner such as [llama.cpp](https://github.com/ggml-org/llama.cpp), which supports local quantized inference and grammar-constrained output. Keep the public response at `{"label":0}` or `{"label":1}` using a compatible grammar/schema where available. This is a local model invocation, not a paid remote service.

**Quantization** stores weights with fewer bits to reduce memory use; it can affect quality. A nominal 4-bit 4-billion-parameter weight array is roughly 2 GB before quantization metadata, caches and runtime overhead. This does not establish actual RAM use or speed. With 32 GB RAM, compute throughput and long-article context may be more limiting than raw weight size. Do not equate a model fitting in memory with efficient labeling.

Start with the accepted binary policy prompt, remove remote-API-specific instructions, and keep the input/output contract. Freeze language, examples, chat template, context handling and generation mode. Use a short label-only output; avoid long generated rationales. Non-thinking variants/modes are worth measuring because generation time matters. Prompt quality must be checked again on the chosen local model.

The old checker scored the relative probability of yes/no tokens; that is an alternative **scoring adapter**, not the same thing as generating JSON. If studied, version it separately, validate full tokenization of both candidate answers and do not mistake a relative token score for a calibrated probability that the label is correct. Keep final labels integer `0/1`; teacher scores are optional metadata, never a third class.

### Measure before scaling

On 50–100 pilot/development articles spanning length and language, record model revision/quantization, input tokens and coverage, prompt-processing time, output time, median and 95th-percentile article latency, peak RAM, output validity and accuracy against human labels. Use realistic long bodies, not only headlines. Keep the model loaded across requests; cache by content hash, policy/prompt version and model/runtime revision. Find a thread setting empirically rather than launching one full model per CPU core.

Budget illustration for a single pass over 5,000 articles:

| Measured mean per article, if observed | Total sequential labeling time |
| --- | ---: |
| 10 seconds | About 14 hours |
| 30 seconds | About 42 hours |
| 60 seconds | About 83 hours |

These are conversions of hypothetical timings, not measurements on this machine. Long contexts, repeated passes and chunk reconciliation can increase work. Use actual article latency for planning; output tokens per second alone is insufficient.

### Recommended teacher budget

First compare on 50–100 development examples. If quality is useful, label a stratified training pilot of roughly 300–500 articles. After E1/E2 exist, spend additional teacher/human effort on their disagreements, boundary errors and diverse new cases, plus a random audit sample. Default budget is targeted work, not automatic double-labeling of all 5,000 articles. Scale only if measured throughput and label gains justify it.

Agreement between Qwen sizes or repeated prompts is not independent proof; their errors can correlate. Human-check some agreement cases as well as disagreements. A failed, invalid or incomplete model invocation is not label `0` and cannot enter the training export as a successful annotation.

## 8. Label creation, learning curves and active learning

### Build a reference before trusting a teacher

1. Read usable title/body without seeing category predictions or teacher answers.
2. Apply the accepted adverse policy and assign `0/1` with source-text evidence in a review sidecar.
3. Have independent second judgments on the pilot and important reference data; adjudicate differences. Where resources allow, double-label the final reference set.
4. Freeze a final reference pool before tuning and do not send it to the teacher to make its training labels.

If final teacher accuracy is to be evaluated, its predictions on that pool are inference outputs only; they never replace the reference labels or feed training. Reference provenance must distinguish human review from a second LLM's agreement.

### Teacher-generated labels are silver

Preserve the original machine label and human corrections, including which policy version governed the decision. Start by comparing human-only training with human-plus-silver training. Consider down-weighting silver examples, but tune weights only on development data. Hard negatives and disputed cases need review rather than automatic deletion.

A teacher can make thousands of errors consistently. Teaching those errors to a student is not successful learning merely because student-teacher agreement is high. Measure both teacher and student against the same independent reference.

### Learning curve

Train E1/E2 at approximately 250, 500, 1,000, 2,000 and 5,000 labeled training articles where available, using nested subsets and fixed group assignments. Repeat promising settings with a small number of seeds to expose instability. Compare quality by class and important slices; more data is useful only if it adds information or reduces uncertainty.

**Active learning** means choosing the next articles to label based on where the current model could learn most. Combine uncertain model scores, disagreement between sparse/semantic classifiers, new sources/languages and embedding diversity. Retain a random acquisition component so the corpus does not contain only strange edge cases. Active-learning selection never reads the held-out evaluation pool.

Synthetic minimal pairs can supplement training: change an actual allegation to a hypothetical warning, or add an acquittal-only ending. Human-check the label after every edit, preserve paired versions in one split and label synthetic provenance. Real representative articles remain the basis of production evaluation. Do not let polished model-written prose dominate the training distribution.

## 9. Long articles: the main semantic design trap

The stored body can be much longer than an encoder input. The [e5-small card](https://huggingface.co/intfloat/multilingual-e5-small) demonstrates a 512-token input limit; a [multilingual MiniLM sentence encoder](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2) has a default 128-token limit. Neither should be assumed to read a complete news article through one default call.

**Token** means a model-specific text unit, not necessarily a word. Audit length using the selected tokenizer and reserve room for the prompt/title. Do not silently truncate the end: allegations can appear first and acquittal or correction last.

For E1, retain the usable full body and preserve negation; do not blindly strip words such as “not.” For E2, create overlapping paragraph-aware chunks that fit the encoder, encode all chunks, then compare document representations built from title, mean/max pooled chunk features and final-chunk features. **Pooling** combines multiple vectors into a document representation. Keep chunk count and coverage diagnostics. Mean/max pooling is a baseline, not proof that the model reconciles distant claims and outcomes.

For E3, a small document-level classifier over ordered chunk embeddings can be studied before expensive encoder fine-tuning. This allows later outcome context to influence the article decision. If a full trained hierarchical encoder is needed, benchmark its CPU training cost first. Do not assign label `1` to every paragraph of a positive article: many paragraphs may be neutral or resolution context.

For the teacher, use full text when the actual configured context and latency budget allow it. For longer articles, a separate experiment can extract exact event/outcome passages from chunks and perform document-level reconciliation. Retain actor/case linkage and validated original quotes; generated summaries can omit negation. The final target is still binary, even if intermediate research metadata is richer. Do not implement a simple “any chunk positive means article positive” rule when resolution-only handling can reverse the interpretation.

Long input is not the same as missing data. Route it to a coverage-preserving path or keep it pending processing, rather than silently removing it from all evaluation and claiming complete coverage. Report the fraction of eligible articles each candidate actually processed fully.

## 10. Evaluation design — what we will test later

This section specifies future model experiments. No tests or model evaluations were run for this design request. Repository rules also reserve implementation-test changes/execution for explicit commit authorization.

### Split before fitting or teacher tuning

- Group exact duplicates, syndicated/near-duplicate stories, physical versions, known same-case stories, and synthetic variants before splitting. No related group crosses training/development/final partitions.
- A source-held-out experiment measures unfamiliar publishing styles. A later time window measures newer traffic. They answer different questions and should be reported separately.
- If a case crosses a time boundary, keep its group in one partition or exclude it from that temporal comparison; document the choice.
- Fit vocabulary, encoders' task-specific parameters, calibration, sampling and thresholds using training/development only. A frozen pretrained encoder may encode final articles at evaluation, but do not use final labels/text distribution for task-adaptive training.
- Never build prompts from final-set mistakes and continue calling that set untouched. If used for development, replace the final set with genuinely fresh cases.

### Measure the actual binary task

| Metric | Meaning / why we need it |
| --- | --- |
| Precision for label `1` | Of flagged articles, how many meet the definition? Controls false alerts. |
| Recall for label `1` | Of truly adverse articles, how many are caught? Controls missed events. |
| False-omission rate | Of articles labeled `0`, how many are actually adverse? Shows risk in suppression. |
| Macro-F1 and class counts | Summarizes both classes while exposing imbalance. F1 combines precision and recall. |
| Precision-recall curve / average precision | Shows ranking and threshold trade-offs on the stated distribution. |
| Output/input coverage and errors | Separates binary performance from extraction/runtime failures. |
| CPU median/p95 latency, throughput, peak RAM, artifact size | Determines practical deployment and batch completion. |

Report by source, language, body length, event stage, victim-centered reporting, resolutions and mixed actors. Report natural-distribution and challenge sets separately. Balance during training if useful, but do not turn a 50/50 evaluation into an unqualified claim about production precision. If calibration is used, fit it on appropriate representative development data or account for the known sampling design.

Use group-aware uncertainty estimates, such as case-group bootstrap intervals, rather than pretending every copied article is an independent observation. With only a handful of positive cases, high recall is weak evidence. A few hundred positive cases improve measurement but do not guarantee a narrow interval or prove a 95% minimum recall. Publish counts and confidence bounds instead of only percentages.

### Essential challenge cases

Fraud allegation versus education; formal probe versus vague speculation; denial versus independent clearance; acquittal-only versus another unresolved case; victim versus perpetrator; accidental outage versus ransomware; state ownership/public office without misconduct; civil dispute without included conduct; historic conduct; negation in the final paragraph; several actors with different outcomes; source boilerplate; short official notices; code-switching; unseen publishers; malicious instructions embedded in article text.

### Promotion rule

First agree acceptable missed-event and false-alert rates, daily volume, CPU processing window and review capacity. Provisional development goals can be around 95% recall and 90% precision, but these are planning ambitions, not approved or measured acceptance criteria. Require adequate positive counts and interval reporting. Choose a simpler candidate if the more complex model's practical gain is small or statistically unclear. A model that cannot finish the available batch within its window fails the operational requirement even if its score is better.

Choose among experiments using development results. Evaluate the frozen winner on the final reference; if it fails, return to development and reserve a new final comparison for the next claim. Do not repeatedly optimize against the final set.

## 11. Broader approaches considered and research basis

| Approach / primary source | Lesson for PDG-115 | Decision |
| --- | --- | --- |
| [Baselines and Bigrams](https://aclanthology.org/P12-2018/) | Small sparse classifiers deserve a real benchmark. | E1, immediate. |
| [SetFit](https://arxiv.org/abs/2209.11055) | Small sentence encoders can adapt with limited labels. Its general few-shot results do not establish long-document behavior here. | Conditional E3; validate article representation. |
| [Knowledge Distillation in Automated Annotation](https://aclanthology.org/2024.nlpcss-1.9/) | LLM-generated training labels can support supervised classifiers, but label quality needs independent assessment. | E5 after teacher pilot. |
| [Distilling knowledge](https://arxiv.org/abs/1503.02531) | Teacher outputs can supervise a smaller model. | Compare hard-label training first; optional soft-score ablation, without changing final classes. |
| [Distilling Step-by-Step](https://arxiv.org/abs/2305.02301) | Explanations can provide additional training supervision in a different architecture. | Not the initial CPU design; do not feed teacher-generated reasons as production-only-missing text features. |
| [CanDist](https://aclanthology.org/2025.acl-long.139/) | Forced single teacher annotations can be noisy on difficult examples. | Use as a warning and future research lead. Do not adopt its candidate-label output contract here; accepted targets remain `0/1`. |
| [Confident Learning](https://arxiv.org/abs/1911.00068) | Out-of-fold predictions can help rank likely labeling errors. | Optional review prioritization; not automatic relabeling or deletion of difficult minorities. |
| [Snorkel](https://arxiv.org/abs/1711.10160) | Several imperfect labeling heuristics can support weak supervision. | Optional if useful high-precision heuristics exist; heuristics are not gold and cannot gate away all negatives. |
| [Don't Stop Pretraining](https://arxiv.org/abs/2004.10964) | Unlabeled in-domain text may help domain adaptation. | Defer until data/label errors and simple candidates are understood; extra CPU training may not be worthwhile. |
| [QLoRA](https://arxiv.org/abs/2305.14314) | Low-bit weights plus trainable adapters reduce LLM fine-tuning memory. | Does not remove compute cost; defer generative-LLM training on this CPU-only machine. |
| [Adverse Media Mining](https://arxiv.org/abs/2110.11542) | Risk relevance and entity/query relevance are distinct parts of screening. | Preserve article-level target and separate actor matching. |

Other possibilities: NLI/zero-shot definition matching as an optional small dev comparison; a sparse/semantic ensemble if complementary errors justify it; a local-LLM fallback only if measured batch latency allows it. None is required to complete the first research cycle. Do not build a graph of actors/events before establishing whether article-level classification meets the need.

## 12. Reproducibility and proposed research outputs

Use a new research-only environment; do not change pinned production dependencies to try models. Initial layout could be:

```text
research/adverseNews/
  policy/                 frozen definition, examples, local-teacher prompt
  data/manifests/         export provenance, content hashes, split/group IDs
  data/annotations/       original binary labels, reviewed corrections
  experiments/            preparation, training, evaluation entry points
  results/                metrics, errors, latency, learning curves
  artifacts/              selected vectorizer/classifier or encoder package
  README.md               exact reproduction and decision record
```

Raw/private article exports and large caches should follow the repository's data policy and stay out of commits where appropriate. This layout is a proposal; no scripts/environment were created.

Per article preserve original source identity/text hash, preparation version, split/case group, provenance, binary label and label origin. Separately record policy/prompt hashes, teacher/model revision, quantization, actual context coverage, accepted review corrections and run settings. Export only successful eligible `0/1` rows for training.

Every experimental result needs data/split hashes, model revision, preprocessing/chunking settings, vocabulary configuration, seed, class weighting, decision threshold, thread/runtime settings, elapsed time, memory and evaluation counts. Cache deterministic features by their actual inputs and version; a policy change invalidates labels, and a model/input-recipe change invalidates features or predictions as applicable.

For production packaging, include exact feature ordering/vectorizer, tokenizer/encoder where used, weights, label mapping, policy and calculation identity, input-coverage handling and selected threshold. CPU quantization/ONNX export is a later optimization to evaluate against the unoptimized model; do not assume no change in decisions.

## 13. Delivery phases and immediate next step

| Phase | Deliverable | Gate before continuing |
| --- | --- | --- |
| A | Database export contract and article/source/language/length inventory | Know usable counts, duplicate groups, likely prevalence and coverage gaps. |
| B | 300–400 article pilot, binary policy decisions, independent human reference | Definition applied consistently; victim/resolution examples included. |
| C | E1/E2 comparisons on first approximately 1,000 training articles | Establish economical baseline and failure types. |
| D | Small CPU Qwen quality/latency comparison | Decide whether any teacher is useful and how many calls fit the budget. |
| E | Targeted labeling/review; learning curve up to approximately 5,000 if needed | Demonstrate whether added data or teacher supervision improves held-out development behavior. |
| F | Conditional E3/E5; one chosen recipe and threshold | Gain justifies CPU cost and handles long documents. |
| G | Frozen final reference evaluation and packaged artifact | Quality, coverage and batch throughput acceptable with uncertainty reported. |
| H | Separate post-processing integration and shadow audit | Assessments are source-versioned, failures visible and rollback possible before filtering. |

The next action is to obtain a broad article export and build the pilot inventory/reference. No paid API or GPU is a prerequisite for that step. The choice of final model remains an experimental outcome; the current recommended path is **classical baseline -> compact semantic classifier -> optional targeted local teacher and student training**, not mandatory LLM distillation.

## 14. Decisions a senior engineer should be able to explain

1. We need about 2,000 varied positive training examples as an initial expansion target, not 5,000 automatically trusted category labels. We also need useful negatives and an independent reference; learning curves decide whether to expand.
2. CPU-only hardware changes the economics: a compact semantic classifier can be practical, while a local LLM may be useful mainly as a budgeted offline labeler. Teacher quality and throughput must both be measured.
3. Success means agreement with the business definition on fresh source/case/time data at acceptable CPU cost, not agreement with a teacher or a strong score on leaked/synthetic-only data.
