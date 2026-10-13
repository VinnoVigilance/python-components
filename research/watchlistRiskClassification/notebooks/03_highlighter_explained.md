# Notebook 03 explained — the highlighter

Companion to `03_highlighter.ipynb`. What was done, how, why, the results, and likely senior questions.

## 1. The big picture

Notebook 02 showed that models reading the **whole record** fail on new lists mainly through **precision**: they learn
each list's writing style and add wrong labels. So the design changes the unit of work:

```
record ─► L1 lookup (codes → labels)                          60% of labels, near-perfect
       ─► L2 split into evidence units                        "TAX EVASION", one sentence, one charge
       ─► L3 trigger words flag (unit, label) candidates      ← notebook 03: must not miss anything (recall)
       ─► L4 decider: yes / no per candidate                  ← Part C: must be right (precision)
       ─► final labels = lookup ∪ decider's yes, each with its evidence sentence
```

A highlighter is like a person running a marker over a document before an expert reads it: cheap, fast, and it must not
skip anything important. Being over-eager is fine — the expert (decider) crosses out wrong highlights.

## 2. Terms used

| Term | Plain meaning |
|---|---|
| **Evidence unit** | one small piece of a record: a charge phrase (*"Murder"*), one sentence of a narrative, one occupation line |
| **Trigger word** | a word or phrase that suggests a label (*launder\** → Money Laundering). Whole-word match; `*` allows endings |
| **Candidate** | a (unit, label) pair flagged by a trigger word, to be checked by the decider |
| **Candidate recall** | of the labels the lookup misses, the share whose record has at least one flagged unit for that label. Whatever is not flagged is lost for good |
| **Flag precision** | of the flagged (record, label) pairs beyond the lookup, the share that are real labels. Sets the decider's workload |
| **Decider** | the step that reads each candidate unit with the label's definition and says yes / no (an LLM now, later a small model trained on its answers) |
| **Cache** | identical units are answered once and the answer reused |

## 3. How it was built

**Splitting** (`scripts/highlighter.py`, `split_units`): each record line is a field (`Programs:`, `Remarks:` …);
`Source list`, `Entity type` and ID fields (numbers, IDs, website, e-mail, phone, dates) are dropped; the rest is split at
`||` (items), `;` (charge lists) and sentence ends. Example — Interpol: `Programs: Law Enforcement ; Murder ; GE` →
units *"Law Enforcement"*, *"Murder"* (*"GE"* is dropped: no 3-letter word).

Dropping ID fields removes a known false trigger in a principled way: *"Tax Identification Number"* is an ID field, not
evidence of Tax Crime.

**Trigger words**, two lists kept apart so the result stays honest:

| List | Source | Example (Tax Crime) |
|---|---|---|
| v1 taxonomy | key words of the taxonomy's *Sample Indicators* | *tax evasion, tax fraud, tax return\*, taxable income* |
| v2 conduct words | general conduct words, official titles, family words, Spanish / French / German / Portuguese charge words | *tax, taxes, evasion fiscal, fraude fiscal, steuerhinterziehung, vat, excise* |

Matching ignores case and accents (*Evasión* = *evasion*) and treats hyphens as spaces (*state-owned* = *state owned*).
Whole words: *tax* does not match *taxi*.

**Scored on** the 610 label instances the lookup misses on **15 labels**: all except the lookup-only Sanctions,
Regulatory, Law Enforcement, and the **5 labels not covered yet** (RCA 1 example, State-Owned 4, Securities 5,
Environmental 7, Jurisdiction Risk 0 — too few examples to build or measure anything, and no lookup rule gives them;
they stay in the taxonomy as future labels). Trigger words do not learn from data, so seen and unseen
lists score the same. The only learned part (section 5) is tested on the unseen-list folds.

## 4. Results

| | v1 taxonomy | **v1 + v2** | + learned words (p ≥ 0.7) | + learned words (p ≥ 0.5) |
|---|---|---|---|---|
| Candidate recall | 74.4% (454 / 610) | **94.8% (578 / 610)** | 96.4% | 97.7% |
| Flag precision | 0.74 | 0.48 | 0.29 | 0.19 |
| Questions per record | 1.9 | **3.9** | 6.5 | 10.9 |
| Share of text the decider reads | 28% | 49% | 61% | 69% |

* **By label (v1 + v2):** 12 of 15 labels ≥ 97%, including Judicial, Fraud, Corruption, Human Rights, Tax Crime,
  Terrorism, Proliferation and Cybercrime at 100%. Weak: Violent Crime 94%, Organized Crime 84%, **PEP 76%**.
* **By segment:** wanted lists 96.3%, sanctions & terror lists 91.8%, regulatory 100%. No list below 79% (EU sanctions
  lists, because of PEP job titles).
* **Learned words** (from training lists only, tested on held-out lists) add 1.6–2.9 points of recall but 67–179% more
  work, and the learned lists are mostly noise (for Theft: *alias, build, having, the most*). **Not used.**
* **Workload per 1,000 records:** 2,872 unit calls (1,619 with a cache); 270 records need no call at all.
* **Not covered yet**, for information only: the highlighter would find Securities 5 of 5, Environmental 7 of 7,
  State-Owned 2 of 4, RCA 0 of 1 — not part of any score.

### The 32 misses

| Label | Missed | Typical text | Fix (next version) |
|---|---|---|---|
| PEP | 16 | *Commander of the Air Force, IRGC*; *Brigadier General*; *Member of the State Duma* | official-title rule (rank + organisation words) |
| Violent Crime | 6 | *VIOLACION*; *trois meurtres*; *intentionally injures the person of another* | more foreign / legal charge words, plurals |
| Organized Crime | 6 | *CRIMINAL ORGANIZED GROUP*; *committed by organized group* | word-order variants |
| Others | 4 | *misappriopration* (typo); *transferring criminal property*; *knock-offs and fakes* | targeted words |

These words were deliberately **not** added after seeing the misses — that would inflate the score. They go into the
next version, judged on the human-checked test set.

## 5. Likely senior questions

**Why not just use the notebook 02 model?** It finds labels in a whole record but adds many wrong ones on new lists
(precision 0.46 at best). The highlighter is not a classifier: it only decides *where to look*. Deciding is a separate step.

**95% — what happens to the other 5%?** The decider never sees them, so they are lost. That is why the remaining misses
are listed one by one: most are one pattern (PEP job titles) with a clear fix.

**Flag precision is only 48% — is that bad?** No, it is by design. The highlighter is tuned for recall; the decider removes
wrong flags. Precision only sets the workload (3.9 questions per record).

**Why not learn trigger words from the data?** Tested: on held-out lists it adds 1.6 points of recall for 67% more work, and
the learned words are list noise. Hand-curated words are readable, auditable and list-agnostic.

**Is the score honest?** v1 is from the taxonomy only. v2 was written with general domain knowledge after reading some
records, so it is slightly optimistic. The misses were not used to tune it. The final check is the human-checked test set.

**What does it cost?** About 2.9 decider calls per record (1.6 with a cache) on short pieces instead of whole records. For
300k records ≈ 860k calls before caching; time per call is measured in Part C.

## 6. Takeaways

1. A cheap, list-agnostic highlighter (split into units + trigger words) puts **95%** of the text layer's labels in
   front of the decider, at ~4 questions per record, on every list type.
2. It is recall-first by design (48% of flags right); the decider must supply the precision — that is where bank-grade
   reliability will come from.
3. The remaining gap is mostly PEP job titles and foreign-language charges, fixable with targeted rules; data-learned
   words were tested and rejected.

*All numbers in this file come from `03_highlighter.ipynb` outputs (`results/highlighter_per_label.csv`,
`results/highlighter_answer_sheet.csv`).*
