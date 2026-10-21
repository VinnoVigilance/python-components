# Notebook 01 explained — EDA and traditional ML

Companion to `01_eda_traditional_ml.ipynb`. What was done, how, why, the results, and likely senior questions.

## 1. The big picture

**Goal:** give every watchlist member the right risk labels (23 possible), cheaply and bank-grade reliably.

```
watchlist record
      │
      ├─► Layer 1: LOOKUP (rules)      list / measure / programme / FBI category → labels
      │        e.g. OFAC-SDN → Sanctions;  programme "SDNTK" → Trafficking
      │
      ├─► Layer 2: TEXT MODEL (ML)     reads the record text → labels rules cannot give
      │        e.g. remark "...laundered the proceeds..." → Money Laundering
      │
      └─► FINAL LABELS = lookup ∪ model      (∪ = union: everything either layer says)
```

Rules are free, exact and explainable, but only work where a code *means* a conduct (an OFAC-SDN record is always
Sanctions). "Judicial" or "Fraud" are only written in free text, so something must read it.

Notebook 01 asks: how good is the cheapest reader (traditional ML) at the part rules cannot do? Later steps
(encoders, LLMs) are measured the same way so they can be compared fairly.

## 2. The data

| Item | Meaning |
|---|---|
| 900 records | watchlist members from 29 lists |
| Text | each record rendered into lines: `Source list:`, `Programs:`, `Measures:`, `Remarks:`, … |
| Labels | final agreed labels: labeller agreement + 40 decided disputes + lookup-rule labels + 1 correction |
| 0/1 matrix | 900 rows × 23 columns; 1 = record has that label (the format ML needs) |
| Parts A/B/C/D | how the sample was built. **D = random**, the only part with real-world frequencies; B was keyword-picked to add rare labels, so it is easier than reality |

EDA findings:

* Strong imbalance: Sanctions 464 positives, Tax Crime 17, RCA 1, Jurisdiction Risk 0.
* The lookup produces **60% of all label instances** (a label instance = one record-label pair; 1,629 in total).
  **The other 40% (645) must come from the text model.**
* Wanted lists are the hardest segment: 2.9 labels per record, long multilingual charge text.
* Labels travel together, e.g. 96% of Judicial records also have Law Enforcement.

## 3. How the experiment is run

### Step 1 — Text to numbers (features)

**TF-IDF** (term frequency × inverse document frequency): a word scores high if it appears in this record and is rare
across all records. *"laundering"* in a remark → high; *"the"* → near zero.

| Type | Example for "laundering" | Strength |
|---|---|---|
| Word n-grams (1–2 words) | `laundering`, `money laundering` | readable |
| Character n-grams (2–5 letters) | `lau`, `laun`, `aund`, `ring` … | also matches *launder / launderer*, other languages, codes |

### Step 2 — One yes/no classifier per label (one-vs-rest)

A record can have several labels, so 18 separate yes/no models are trained (one per label with enough data) on the
same features. **Logistic regression (LR)** gives every feature a weight; the weighted sum is a score. Fast and readable.

### Step 3 — Imbalance, handled twice

1. `class_weight="balanced"`: a missed rare positive costs as much as many missed common ones. Without it the model
   learns "always say no to Tax Crime" (98% accurate, useless).
2. A **threshold per label**: the score above which we say "yes", chosen to maximise F1 for that label.

### Step 4 — Fair testing (cross-validation)

* **5-fold cross-validation:** split 900 into 5 groups; train on 4, test on the 5th; rotate. Every record is tested
  once by a model that never saw it (**out-of-fold, OOF**).
* **Multi-label stratified folds** (`iterative-stratification`): every fold gets a fair share of every label
  (Tax Crime 3–4 per fold), instead of a random split that could put all 17 in two folds.
* **Cross-fitted thresholds:** fold 3's threshold is chosen on folds 1, 2, 4, 5 — a record's own label never sets its own cut-off.

### Step 5 — Metrics

| Metric | Plain meaning | Bank angle |
|---|---|---|
| Precision | of alerts raised, the share that are real | false alarms cost analyst time |
| Recall | of real cases, the share found | **misses are compliance risk — recall matters most** |
| F1 | balance of both | comparing models |
| Micro-F1 | pools all label instances; big labels dominate | overall product quality |
| Macro-F1 | average over labels; each label equal | are rare labels OK too? |
| PR-AUC | how well positives are ranked above negatives, any cut-off | model quality without threshold |
| R@P90 | recall if we demand 90% precision | distance from bank-grade |
| 95% interval (Wilson) | range the true value probably lies in | 17 positives → wide range |

### Step 6 — Two views of the score

* **Whole label set:** lookup ∪ model vs all 23 labels — what the customer sees.
* **ML part only:** only the 645 instances the lookup did *not* give. Recall = how many the model found;
  precision = of labels the model *added* beyond the lookup, how many are correct. The honest model quality.

### Step 7 — Leave-one-list-out (LOLO)

Hold out one whole list (e.g. Interpol), train on the other 28, test on it; repeat for all 29.
Simulates onboarding a new watchlist.

## 4. Approaches and results

Headline: system macro-F1 on the 15 text-layer labels (lookup ∪ model). **Lookup alone = 0.335.**

| # | Approach | How | Result | Why |
|---|---|---|---|---|
| M1 | word TF-IDF + LR | words / pairs → weights | 0.784 | misses word forms, languages, codes |
| **M2** | **char TF-IDF + LR** | letter chunks → weights | **0.825** | robust to languages and spelling |
| M3 | word + char + linear SVM | max-margin classifier | 0.811 | ≈ M2: features matter more than classifier |
| M4 | word + Complement Naive Bayes | count-based probabilities | 0.650 | too crude for overlapping labels |
| M5 | field-segmented TF-IDF | separate vocabularies for codes / narrative | 0.773 | Interpol keeps charges in `Measures`, split cuts meaning |
| M6 | LSA embedding + LR | TF-IDF squeezed to 300 dense topics | 0.816 | compression loses rare decisive words |
| M7 | narrative only | no list name, programmes or measures | 0.568 | lost real information |
| **M8** | **M2 without list name** | only `Source list:` removed | **0.823** | equal, and better on new lists → **recommended** |
| M9 | M8 without programme lines | | 0.696 | programme text carries real words — keep it |

Cost: full 5-fold run of M2 ≈ 20 s on a laptop CPU; prediction is microseconds per record.

### Whole set vs ML part (M2)

| | Precision | Recall | F1 |
|---|---|---|---|
| Whole set, all 23 labels | 0.92 | 0.88 | 0.90 (micro) |
| ML part only | 0.79 | **0.71** | 0.75 |
| Macro-F1 with / without the 5 rare labels | | | 0.67 / 0.85 |

The whole set looks strong because 60% of instances come from near-perfect rules. The model itself misses about
3 in 10 of the labels only it can give. (ML-part recall is 0.73 over the 18 modelled labels, 0.71 over all 23,
because the rare labels count as misses.)

### Per label (M2)

* Solved by lookup: Sanctions, Regulatory, Law Enforcement (≥ 0.96).
* Good, not bank-grade (F1 0.83–0.93): Fraud, Terrorism, Proliferation, Human Rights, Violent Crime, Money Laundering, Cybercrime, Judicial.
* Weak (≤ 0.80): Trafficking, Corruption, PEP, Forgery, Organized Crime, Theft 0.67, Tax 0.63.
* No text-layer label reaches 0.95 recall.

Lookup and model complement each other: Human Rights recall 0.32 (lookup) → 0.82 (model) → **0.94** (both).

### Segmentation

1. Quality by segment: officials / business register 100% of records fully right, regulatory 95%, sanctions 75%, **wanted lists 50%**.
2. One model per segment is **worse** than one global model (0.79 vs 0.82): splitting 900 records costs more than specialising gains.
3. Unsupervised clusters group records by source writing style, not by conduct (ARI 0.21) — useful for choosing test samples, not for labelling.

Decision: one global model; segments for reporting, thresholds and review routing.

### Unseen lists — the main weakness

| | ML-part recall, list seen | ML-part recall, list unseen |
|---|---|---|
| M2 (with list name) | 0.74 | 0.43 |
| M8 (no list name) | 0.74 | **0.48** |

Collapses on new lists: Forgery 0.62 → 0.03, Tax 0.65 → 0.12, Corruption 0.76 → 0.29. TF-IDF only knows words it
has seen, and each list writes its own way. Real error: Interpol *"Evasión"* (Spanish for *escape*) tagged Tax Crime
because its letters look like *evasion*.

## 5. Likely senior questions

**Why not an LLM for everything?** The lookup gives 60% of labels free, exact and auditable. LLMs cost per record, are
slower and can vary between runs. Models are used only where rules cannot decide, and each option is measured the same way.

**Whole-set F1 is 0.90 — good enough?** It is inflated by the rules. The model's own part (40% of labels) finds only
71%. For AML, recall on the model's part decides.

**Why train on labels the lookup already gives?** In production the model sees the full record. Removing those
positives would teach it that a terrorism-programme record is *not* Terrorism. Train on the full truth, **score** the ML part separately.

**How were 23 imbalanced labels handled?** Balanced class weights, a threshold per label chosen on other folds,
stratified folds so every fold holds every label, confidence intervals. Labels with < 15 positives are reported as
not measurable instead of given fake scores.

**How do you know scores are not inflated?** Out-of-fold scoring, cross-fitted thresholds, ML part as headline, an
unseen-list test. Remaining caveat: labels came from LLMs and part B was keyword-chosen; final numbers come from a human-checked test set.

**Did segmentation help?** Not as separate models (worse). Useful for reporting, per-segment thresholds and review routing.

**Rare labels?** 1% of instances. Mostly a rules job: Jurisdiction Risk = country lookup; State-Owned and RCA = rules on
relationships; Securities / Environmental = keyword search of the full watchlist data to find and label more examples.

**Next step?** Pre-trained (ideally multilingual) encoders: they already know language, so they should close the
unseen-list gap TF-IDF cannot. Same folds, unseen-list ML recall as headline.

**Which traditional model to keep?** Char TF-IDF + LR without the list name (M8): cheap, explainable, best-equal
in-distribution, better on new lists — the baseline every later model must beat.

## 6. Takeaways

1. Rules give 60% of labels perfectly; the model must give the other 40%. Judge the model on that 40%: recall 0.71, precision 0.79.
2. Char TF-IDF + logistic regression without the list name is the best traditional model — good on many labels,
   weak on overlapping crime labels, poor on new lists (recall 0.48).
3. New-list generalisation is the deciding problem — why encoders/LLMs come next and why unseen-list recall is the selection metric.
