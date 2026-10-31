# PDG-115 — implementation handoff

## Final traditional ML research recommendation (2026-10-29)

The user requested a stronger blind model review and final classical ML
recommendation. A fresh GPT-6 Astra/high-reasoning task reviewed 100 full-text
articles: 60 stratified random recent and 40 deliberately difficult publisher
transfer cases. Outputs with quotes, uncertainty flags, and a comparison against
the existing labels and three ML finalists are in
`research/adverseNews/data/review/strong-review-v1/`. Seven judgments were
uncertain. The existing 5,000 target labels were not overwritten. Thresholds
were selected on pre-2025 out-of-fold scores and compared on 2025–2026 news;
simple finalist voting was also assessed. The user prioritized unseen-publisher
transfer, so the recommendation is LSA 200 + RBF SVM C=3 at default threshold
zero for a human-assisted research pilot, with LSA 100 C=3 and character linear
SVM retained as challengers. The measured
trade-offs, evidence and remaining production gate are in
`research/adverseNews/results/FINAL_TRADITIONAL_ML_RECOMMENDATION.md`.

## Expanded model screen complete (2026-10-29)

The assembled 5,000-row proposed-label corpus has been screened with 46
classical configurations and 394 model/split fits. Three group folds used 4,053
pre-2025 articles; 947 articles dated 2025–2026 were held out chronologically;
six older publishers were held out in turn. The strongest publisher-transfer
family was LSA plus RBF SVM (200 components, C=3, mean publisher F1 0.811),
with the 100-component C=3 variant slightly stronger on newer news (F1 0.937).
UK_GOV transfer remains weak. Full results and interpretation:
`research/adverseNews/results/EXPANDED_MODEL_SCREEN_REPORT.md`; all 46 model
rankings: `research/adverseNews/results/expanded-screen-v1/model-comparison.csv`.
The labels remain proposed silver; production accuracy needs independent blind
human reference labels and final case-safe splits.

## Current model-screen results (2026-10-28)

The user approved exploratory model training and broad classical comparisons.
All 62 configured variants completed both 5-fold stratified automatic-group CV
and an eight-fold leave-one-publisher-out stress test. The first evaluation's
best mean F1 was 0.904 for hybrid word/character TF-IDF with PA-I-style SGD; the
publisher-held-out best mean F1 was 0.763 for LSA logistic regression, with LSA
RBF SVM close at 0.759. The gap and large publisher variation show that random
article folds do not establish publisher transfer.

Full analysis: `research/adverseNews/results/MODEL_SCREEN_REPORT.md`; rankings
and prediction-level artifacts are beside it. No tests were run or added. The
500 targets are mostly teacher-derived (473 model agreements and 27
user-reviewed/adjudicated labels), automatic grouping/same-case review remain
provisional, and there is no independent blind human reference. These results
are exploratory and do not establish production accuracy. The diverse 5,000-row
proposed-label corpus is now assembled below; it enables broader model
comparison but cannot remove teacher bias without case review and an independent
answer-blind human reference.

## Final 5,000-row adverse-news label pool (2026-10-29)

The user authorized GPT-6 Luna / low-reasoning labels for 4,500 additional
articles. The selection is at
`research/adverseNews/data/labeling/expansion-4500-v2/`: 4,500 unique prepared
groups, all 10 available datasets and 9 publisher sources, in 477 full-text
packets of up to 10. Two oversized individual articles are kept intact. The
selection is source-balanced within available non-reserved groups and spread
across publication years/months. The reference-reserved pool was excluded.
There are 3,641 provisional training candidates and 859 provisional dev
candidates; these are not frozen final splits. Most language metadata is
unknown and remains so.

Labeling task `01a182cf-17dc-77d2-8010-d5edbcb4bd14` completed all 477 packets.
Checkpoint and immutable packet returns are in
`research/adverseNews/data/labeling/expansion-4500-v2/returns/gpt6-luna/`.
Expansion raw labels are 2,486 negative / 2,014 positive; a separate correction
sidecar makes the final expansion target 2,488 negative / 2,012 positive. The
combined dataset is
`research/adverseNews/data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl`;
its manifest contains integrity hashes and source, dataset, language and
label-origin counts. All 5,000 content hashes and prepared groups are unique.
The selection covers all 10 datasets and nine publisher sources; quotas are
balanced among eligible rows, so label mix is not a database prevalence
estimate. Language metadata is unknown for 4,199 records.

The 5,000 rows remain proposed silver labels, not a verified benchmark. No
independent answer-blind human reference exists; same-case review and final
case-safe splits remain open. The 5,629-group reserved reference pool was not
used. Two policy corrections are recorded separately in
`returns/gpt6-luna/review-corrections.jsonl`, while original model responses
remain intact. The finalizer validated packet IDs, labels, hashes and
disjointness before writing the combined files.

## Paired teacher expansion and 18 adjudications complete (2026-10-28)

The user skipped the prepared six-case agreement audit and authorized both
DeepSeek and GPT to label another 400 articles. The original four disagreements
(015,045,078,087) were accepted as 1 after discussion; the other 91 original model
agreements remain proposed silver. Original 96/100 is raw model agreement, not
accuracy. Keep original responses/provenance and human-adjudication status.

`research/adverseNews/data/labeling/paired-expansion-400-v1/` contains 400 new
training-candidate articles, disjoint by automatic group from the first 100, in
41 identical packets of up to 10 full articles (60,000-character cap). Both
labelers completed all 400. DeepSeek's aggregate response is preserved at
`returns/deepseek/all-400-as-pasted.txt`; packet returns and strict imports are
also retained. GPT-6 Luna thread is
`01a1824e-326b-7441-935b-6eded0a28bce`. Pair comparison is 382/400 (95.5%) with
18 disagreements: four DeepSeek 1 / GPT 0, fourteen DeepSeek 0 / GPT 1. The user
adjudicated all 18 after answer-hidden review. Their decisions matched DeepSeek
on 16/18 and GPT on 2/18 selected disagreements; this is not an accuracy estimate.
The expansion final-label file is `comparison/final-labels-400.jsonl`; detailed
provenance/rationales are in `human-adjudication-v1/human-disagreement-adjudications.jsonl`.
DeepSeek model/mode was not supplied. Across all 500 articles there are 473
proposed silver agreements and 27 user-reviewed/adjudicated labels. All have a
binary target, but none of the human decisions form an independent representative
production reference. No training/CV started. The six-case agreement audit stays
unused.

The 500 labeled articles support an initial exploratory baseline after explicit
training approval, but are not an independent benchmark or proof of production
reliability. Reserve held-out groups/sources for an answer-blind human reference
set before production evaluation; report slices and do not train on those
reference labels. Never describe model agreement as accuracy.

Combined pilot/expansion input for a future training run is
`research/adverseNews/data/labeling/final-pilot-500-v1/training-pool-500-title-body-labels.jsonl`;
its manifest records 261 positive / 239 negative, 473 proposed silver labels,
and 27 user-reviewed/adjudicated labels. Model features must use title/body only.

## User resolved all four model disagreements (2026-10-28)

User explicitly accepted 1 for AN-P115-015,045,078,087, with policy reasons and
passages. Preserve those decisions after model comparison, not as blind gold.
Records are in pilot-v1/comparison-final/user-decisions-2026-10-28.json and
human-disagreement-adjudications.jsonl. Original model responses remain unchanged.
pilot-labels-with-review-status.jsonl holds all 100 integer labels: 58 positive /
42 negative; 9 human-confirmed after discussion (first five plus these four),
91 proposed silver agreements. No disagreements remain unresolved. Original
agreement is still 96/100, not accuracy. No training approval has been given.
Interpretive boundaries: the specific attributed 1992 electoral-cheating claim
counts; concrete Phase 1 merger investigation/findings count without Phase 2;
armed destruction of voting machines qualifies despite general successful-poll
commentary; specific detention/charges qualify despite a political-motive claim.
Do not generalize these into keyword rules or treat every routine merger check
as adverse. Next: discuss a small agreement audit before broader labeling/model
selection. Base frozen prompt and original evaluations have not been rewritten.

## Both pilot labelers complete: stop before training (2026-10-28)

All 100 user-supplied DeepSeek labels have been saved and strictly imported,
including batches 001–003 in the original pilot-v1 and batches 001–010 in
pilot-remaining-10-v1. No expected IDs are missing or duplicated across sessions.
GPT already completed the same 100 independently. Final comparison is 96/100
agreement, not accuracy. DeepSeek has 54 positives / 46 negatives; GPT 58 / 42.
Disagreements are 015,045,078,087, each DeepSeek 0 / GPT 1. Neither label is accepted
automatically. Full texts and GPT audit justifications are prepared in
`research/adverseNews/data/labeling/pilot-v1/comparison-final/review-disagreements.md`;
summary.md/json are beside it. DeepSeek rationale and actual model/mode are unknown.
Next: discuss those policy boundaries and inspect a few agreements before choosing
further labeling. No additional DeepSeek packets required for this pilot. Training,
CV, tests, paid calls, installation and commits remain unapproved/not performed.

## DeepSeek repacked to ten; GPT complete (2026-10-28)

GPT chat completed all 100 articles / 21 original batches (58 positive, 42 negative),
with raw labels, audit sidecars and imports. DeepSeek batches 001–003 are saved in
the original `data/labeling/pilot-v1/` session and imported; its actual displayed
model/mode is still not supplied. First 15 comparison: 14 agreements, disagreement
AN-P115-015 (DeepSeek 0 / GPT 1). GPT cites the historical allegation that Ramos
cheated Santiago of electoral victory in the wealth article. This is a policy
boundary to review, not a case where GPT is assumed correct. Comparison saved in
`pilot-v1/comparisons/through-batch-003.json`.

User explicitly requested ten-article packets. `repack_labeling_packets.py`
created `data/labeling/pilot-remaining-10-v1/` from the original source session,
excluding exactly completed IDs 001–015. Remaining 85 IDs are packed into 10
packets, at most ten articles, capped at 60,000 characters without truncation.
Sizes: 10,7,7,10,10,9,9,9,8,6. Prompt, article IDs/text and old responses unchanged.
Next user packet is NEW session `paste-packets/batch-001-PASTE-THIS.txt`, IDs
016–025. Future returned labels must be saved/imported using that new session,
not overwrite old batch-001. Compare original GPT labels by request article ID,
not batch number. User can paste answers in the parent chat as before. Training
remains paused. No need to relabel completed articles or launch another GPT run.

## GPT labeling now authorized and dispatched (2026-10-28)

The user accepted the first-five policy analysis (0,0,1,1,1), explicitly requested
a new GPT chat to label the 100 pilot articles, and emphasized careful small-batch
processing and prompt fidelity. Created chat `01a18214-3fd6-7492-a36b-2fb978a5a47a`,
title `PDG-115 GPT pilot labeling`, same repository/research branch. It was observed
active and reading policy/pilot inputs. Its scope is all 21 batches / 100 articles,
its own model judgments with concise evidence sidecars and import validation,
without reading human or DeepSeek answers. No paid API calls, tests, training/CV,
commits or expansion beyond 100. Check that chat's progress/artifacts before
claiming labeling is complete; dispatch is not completion.

Original human-first-5.md remains unchanged. User-confirmed corrections are in
data/labeling/pilot-v1/human-confirmed-adjudications.jsonl, explicitly marked as
confirmation after AI discussion, not independent blind human reference.
User is to paste the entire batch-001 packet into a fresh DeepSeek conversation,
search off, optional reasoning mode for the quality pilot, save final JSONL to
returns/deepseek/batch-001.txt and report the displayed model/mode. GPT labeling
is handled in the separate chat; do not tell the user to duplicate it manually.

## Current stop point after metadata merge and packet preparation (2026-10-28)

User explicitly requested the existing `research` branch. The shared checkout is
now on that branch; all task code is under the new `research/adverseNews/` folder.
Uncommitted files were preserved; no commits, training, inference, tests, installs
or downloads. Existing research folders on that branch were not used or changed.

Read `research/adverseNews/START_HERE.md` first for current user-facing instructions.
The Desktop metadata file was moved to `data/raw/adverse-news-uk-metadata-5000.csv`
under that folder. All 5,000 IDs/versions/content hashes matched and URLs/dates are
restored. Language remains unknown where the DB has none. Authoritative prepared
snapshot: `data/prepared/db-selection-v3/`, still 37,732 eligible / 265 blank body /
6,809 working candidates. Older version 2 remains a historical snapshot.

Prepared `data/labeling/pilot-v1/`: 100 unlabeled training-candidate articles, one
automatic group each, across nine datasets, in 21 self-contained website packets
of up to five articles. ADB has no training group in the small working selection;
do not borrow reserved examples just to fill coverage. Full article text and full
agreed policy are included in each packet. New transport `pdg115-website-batch-v1`
adds opaque IDs/JSONL and per-article independence without changing the binary
business rules. Optional blind human files contain five then five more articles.

Recommended user action: optionally judge first five before seeing model answers;
paste the entire `paste-packets/batch-001-PASTE-THIS.txt` into a fresh DeepSeek
chat with search off; save response to `returns/deepseek/batch-001.txt`, report
actual displayed model/mode. For independent second-model comparison, use the
same packet in a fresh ChatGPT chat without DeepSeek answers, save under
`returns/chatgpt/`. Website download is not required; plain copied text is enough.
Do batch 001 first, validate and discuss before continuing all 100. No model
labeling has occurred yet. `import_batch_labels.py` can validate real saved
answers and retain `proposed_silver` provenance; do not manufacture answer files
to run it. Small human review is exploratory, not a final accuracy benchmark.

## Latest user direction and prepared data (2026-10-28)

This update supersedes the original immediate implementation/labeling scope below.
The user approved data preparation/selection through provisional split planning,
and explicitly prohibited training until a later approval. They can label only a
few articles, so the earlier 300–400 human-label target is not an active requirement.
Future comparison must consider publisher-format transfer: group-aware CV,
held-out-publisher evaluation, and suitable time holdouts, across multiple model
approaches. Those are plans only; none has run.

User-provided database exports are now under
`research/adverseNews/data/raw/`: `adverse-news-non-uk.csv` (32,997 records) and
`adverse-news-uk-sample-5000.csv` (5,000 sampled UK records). Raw hashes match the
files moved from Desktop. Prepared outputs and limitations are documented in
`research/adverseNews/README.md`; use `data/prepared/db-selection-v2/` there.
There are 37,732 nonblank-body candidates and 265 blank bodies, without labels.
`working-candidates.jsonl` selects 6,809 distinct-body representatives, capped at
2,000 per dataset. Complete eligible data remains in per-dataset JSONL files.
Automatic groups/split/fold proposals are not a frozen case-safe final reference.

The supplied SQL flattened object paths for array-valued `Sources`/`Dates` and used
`Taxonomy` rather than this DB's `Metadata`. Literal CSV `NULL` values must be
decoded. The selector recovered non-UK URL/publication metadata from the retained
payload; UK compact export lacks that fallback. A later metadata-only lookup for
the selected UK IDs can repair its dates/URLs without exporting bodies again.
Most language metadata remains unknown; no language inference was run.

Preparation entry point: `research/adverseNews/select_exports.py` (stdlib only).
No training, CV, model/teacher inference, downloads, installs, tests, DB writes or
commits. Stop after preparation and discuss the small human-review plan before
the next phase. Earlier `prepare.py`/`contracts.py` are unfinished generic drafts;
the frozen policy/prompt files they expect have not been created.

Updated: 2026-10-28. Workspace: `D:/cloned repos/python-components`.

## User objective and current authorization

Implement a separate adverse-news post-processor through the accepted CPU-first research/training plan. The user accepted the plan and requested a fresh session for further implementation, with all necessary context transferred. Begin with practical offline research/data scaffolding; do not repeat the whole design discussion or wait for the future database export to do independent preparation.

Read these files first:

1. `docs/PDG-115-cpu-research-and-training-design.md` — latest accepted approach, experimental matrix, data sizes, evaluation safeguards, sources and delivery phases.
2. `docs/PDG-115-adverse-news-labeling-guide.md` — full binary definition, exclusions, resolution handling and a self-contained labeling prompt. Preserved from the user's Downloads file for this handoff.
3. `AGENTS.md`, applicable nested instructions, and global RTK guidance before shell investigation.

Both PDG-115 documents are design/proposal artifacts. The guide predates the zero-paid-API constraint: its API prompt is a reusable policy/prompt template, not authorization to use a remote paid service. The newer CPU design governs execution choices.

## Non-negotiable decisions

- Target is exactly integer `0` (non-adverse) or `1` (adverse). No uncertain, unassessable or third class.
- Filter missing, empty, error-page-only and otherwise unusable article bodies BEFORE labeling. Keep filter diagnostics separately; never assign `0` to a missing article or failed model invocation.
- A missing title is acceptable with usable body. Do not discard short complete notices or difficult negatives solely for convenience.
- The label concerns the ARTICLE, not the guilt of every actor. A ransomware-victim story may be `1`; the victim is not thereby a perpetrator. Do not inherit watchlist victim suppression.
- Adverse detection is separate from risk-category classification: no risk-category predictions or labels are required as input, training target or runtime dependency.
- No paid API usage. CPU only, approximately 12 cores and 32 GB RAM, no GPU. Do not design the default around CUDA, GPU-based teacher runs, or large generative-LLM fine-tuning.
- Existing roughly 5,000-article media-risk dataset is NOT available. User described the former approach only; those were category labels, not adversity labels. Do not invent or claim to recover that corpus.
- User will provide database news samples/export later. Existing canonical JSONL examples can support a development inventory; they are not gold-labeled data or evidence of production prevalence.
- No new branch containing `codex`; use descriptive prefixes if a branch is needed.
- Do not add, modify or run tests unless the user explicitly asks to commit. No commit requested. Model experiment/evaluation procedures are designed, not executed or generally authorized to consume resources now. Prepare scripts first and keep bounded read-only source/artifact inspection distinct from test/model execution.
- No model-weight download, package installation, bulk inference/labeling/training or database migration was performed. Do not introduce these as hidden setup side effects.

## Definition summary — retain details in the guide

An article is adverse when it substantively reports a specific real-world instance of alleged/established included wrongdoing, a formal investigation, relevant enforcement action, or an official restrictive measure, supported by enough concrete attributable text.

Included scope: specified crimes and deliberate abuse, relevant criminal/regulatory investigations and enforcement, actual official sanctions measures. Specific allegations can count before conviction. A denial does not automatically erase an allegation. Names of perpetrators are not mandatory if a concrete event is reported.

By themselves, financial deterioration, ordinary civil disputes, general criticism, political/status/ownership information, education/statistics, hypotheticals, accidents and technical failures are not positive. Read the full guide for precise scope and exceptions. Source name or negative tone is not a label.

Resolution-only default: `0` where the article only explains acquittal, dismissal, retraction, exoneration or removal of restrictions, with no independent qualifying matter. Retain resolution-update context separately for future case maintenance. Another unresolved case/actor can make a mixed article `1`. Historical misconduct can qualify; sentence completion/appeal is not automatically clearance. Some policy exceptions remain proposed: resolve them consistently in the pilot rather than silently changing labels.

The existing prompt returns only `{"label":0}` or `{"label":1}`. JSON is a transport envelope; target is the integer. Evidence/review details can be sidecar metadata, never extra classes or generated input features. For local use, keep instructions separate from article text and treat embedded instructions as data. Validate output; failure does not become negative.

## Accepted research order and initial sample targets

1. Inventory eligible articles, source/date/language/length mix and duplicate/case groups.
2. Reserve splits before teacher or classifier tuning. Build a 300–400-article pilot, with a representative random component and explicitly identified boundary cases. Use independent human review to establish reference labels.
3. First training comparison around 1,000 training examples, roughly 300–500 positive where feasible.
4. E1: TF-IDF word/character features + logistic regression and linear SVM; compare binary NB-SVM.
5. E2: frozen compact semantic encoder such as `intfloat/multilingual-e5-small` + classifier; optional sparse/semantic combination. CPU-first candidate, cached embeddings.
6. E4: optional local quantized Qwen pilot, initially 50–100 dev articles to measure both quality and CPU article latency. Candidate models: Qwen3-1.7B and Qwen3-4B-Instruct-2507. Use a CPU-compatible runtime such as llama.cpp, exact weight revision and quantization provenance. No claim they are the newest/best models.
7. Expand toward around 5,000 TRAINING articles if learning curves justify it: approximately 2,000 positive and 3,000 negative. These are provisional planning targets, not guaranteed sufficient counts.
8. E3/E5: conditional small encoder training and targeted teacher supervision. Teacher labels are silver until reviewed; human reference is independent. Prefer selective difficult/diverse teacher calls over automatically labeling the full corpus twice.
9. Keep roughly 400–600 dev articles and an initially 1,000–1,500-article representative final evaluation SEPARATE from training. Add separate positive/challenge coverage if rare positives make natural evaluation insufficient. Do not report enriched precision as production precision.
10. Choose the cheapest adequate model using development data, freeze it, then evaluate on the independent final reference. Integrate only after quality and CPU throughput gates are met.

Training pool composition need not mirror production prevalence, but evaluation and calibration must account for sampling. At 10% prevalence, 2,000 random positives would require around 20,000 usable candidates; estimate actual prevalence from random pilot data. Learning curves at roughly 250/500/1,000/2,000/5,000 determine whether more examples help.

## Important modeling/evaluation traps

- Teacher/student agreement is not accuracy. Human reference cannot be replaced by another LLM's agreement and called independently verified truth.
- No train/dev/final leakage across duplicates, syndicated articles, source versions, same-case groups or synthetic variants. Later-date and held-out-source evaluations answer different questions.
- Freeze final data before model/prompt selection. Final mistakes cannot be reused for tuning while retaining the claim that the set is untouched.
- Preserve late negation and resolutions. Small encoders have short token limits; full articles require explicit chunking/representation design. The e5 example uses 512 tokens; a multilingual MiniLM candidate defaults to 128. Do not silently read only the beginning.
- Mean/max/last-chunk features are baselines, not guaranteed event reconciliation. Do not label every paragraph positive because its article is positive; do not use unconditional any-positive-chunk aggregation when the resolution policy can change the article decision.
- Long article is not missing data. Track coverage and route to a valid path rather than quietly excluding all hard long inputs.
- Keep labels, model explanations, source IDs and teacher identity OUT of text features. Preserve provenance outside model input.
- Report positive precision/recall, false omissions among negatives, class counts, macro-F1, important slices, coverage, median/p95 CPU latency and peak memory. Include statistical intervals and case-group independence.
- Approximate 95% recall / 90% precision are provisional design ambitions, not approved release criteria or observed results. Volume, missed-event tolerance, batch window and human capacity still need agreement.
- No measurements of this CPU's LLM speed exist. Hypothetical 60 seconds/article implies about 83 hours for 5,000 single-pass labels, not a measured forecast.

## Data and research references available

- Current main checkout contains `data/final/*_final.jsonl` for sources such as NBI, PCIJ, PTV, Interaksyon, SEC PH, UK government and ADB case summaries. Config: `config/mediaSources.yaml`. Earlier audit counted 382 records across nine exact configured-source filenames, with nonempty bodies but missing/empty language metadata; re-inventory current files before relying on counts. Do not include duplicate/alternate filename exports twice.
- Existing media model metadata: `intelligence/risk/mediaRiskCategory/models/nbsvm-v1/run.json`; NB-SVM, 11 category labels, 3,396 recorded train rows and 728 validation rows. Not adverse evaluation.
- Historical watchlist research exists READ-ONLY in neighboring checkout: `D:/cloned repos/python-components - Copy/research/watchlistRiskClassification/`. Useful files: README, notebooks/04_checker_explained.md, scripts/checker.py, scripts/student.py, handover/SESSION_2026-10-12_RTX4070_part2.md. Do not edit that checkout or treat it as the unavailable media corpus.
- Old local Qwen teacher used relative yes/no token scores; e5 student used binary cross-entropy with soft targets. Generated rationales sometimes hallucinated or contradicted scores. Old GPU timings are irrelevant to CPU full-article cost. Do not clone token-scoring assumptions without validating tokenization/context.
- Public dataset lead: https://github.com/kristjanr/ut-ml-adverse-media — authors report 977 positive and 642 negative articles and list archives. MIT software license exists; scraped article rights, policy compatibility, text quality and duplicates still need inspection. No raw archive downloaded or independently counted.
- https://github.com/Webhose/adverse-media-dataset — negative-sentiment finance collection, candidate text rather than our gold labels. Check terms.
- Hugging Face rangeva listing redirects to Webz.io and viewer says empty; do not count it as a separate large available labeled dataset.
- KYBench is company-level web investigation, not directly this article binary task. Full literature/model links and limitations are in the accepted CPU design.

## First implementation work to do now

Inspect current repository/instructions/status, then create a small research-only scaffold under `research/adverseNews/` if no equivalent implementation already exists. Keep production integration for a later phase.

Prioritize:

1. README with exact staged workflow and inputs, and a versioned local policy/prompt snapshot.
2. An explicit input adapter/contract for canonical JSONL and future database export. Preserve source identity, full title/body, dates, language and content hashes; no category dependency.
3. Inventory and eligibility tooling that produces counts/filter-reason logs without assigning fabricated labels. Plan deterministic exact-duplicate grouping; document near-duplicate/case-group limitations.
4. Annotation/export schema with integer `0/1`, label origin, policy version, review corrections and split/group manifest. Model-generated labels remain separate from accepted human reference.
5. Split and experiment configuration interfaces that support fixed group assignments, E1/E2 candidates, reproducibility and future metrics. Do not invent performance results or train on placeholder labels.

Bounded inventory of supplied local artifacts can help implement the adapter. The database export is pending, so explain what is ready and what data-dependent work remains. Avoid unnecessary questions or reapproval of already accepted design choices. Do not run tests, install dependencies, download models, launch bulk jobs, migrate the database or commit as part of this initial scaffold.

## Working preferences and repository state

User wants plain answer first, then how it fits, why/trade-offs, concrete examples and definitions of terms. Understanding matters more than brevity; reduce repetitive investigation instead of useful explanation.

Use RTK explicitly for supported routine commands (`rtk git status`, `rtk rg -n`, `rtk git diff --stat`); read global `C:/Users/admin/.codex/RTK.md` once per session before shell investigation. Narrow searches/reads and avoid dumping weights, tokenizers or article corpora.

At the last status check before writing this handoff, only the accepted CPU design was untracked; earlier unrelated staged changes/merge conflicts from previous turns were no longer present. Recheck fresh state, preserve all user changes and never assume historical status is current. Handoff and preserved guide are new uncommitted artifacts. No implementation, tests, model execution or model/data downloads have been performed for PDG-115.
