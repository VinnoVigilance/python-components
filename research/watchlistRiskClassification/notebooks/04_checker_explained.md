# Notebook 04 explained — the checker

Companion to `04_checker.ipynb`. What was done, how, why, the results, and likely senior questions.

## 1. The big picture

```
record ─► lookup (codes)                     Sanctions, Regulatory, Law Enforcement, part of the rest
       ─► highlighter (notebook 03)          cuts the record into pieces, flags pieces with warning words
       ─► CHECKER (this notebook)            reads each flagged piece in context, says yes / no
       ─► final labels = lookup ∪ checker's yes, each with the sentence that justifies it
```

The highlighter only looks for words, so half of its flags are wrong (*"Department for **Combating** Corruption"*).
The checker is the part that reads **meaning**.

## 2. Terms used

| Term | Plain meaning |
|---|---|
| **Language model (LLM)** | an AI model that reads and writes text. Here: `Qwen3-4B-Instruct-2507`, open licence (Apache-2.0), 4 billion parameters |
| **4-bit** | the model's numbers are stored compressed so it fits in the laptop GPU's 8 GB; small quality loss |
| **P(yes)** | how likely the model's first answer word is "yes" rather than "no", between 0 and 1. ≥ 0.5 = yes |
| **Zero-shot** | the model is not trained on our labels; it only gets the label's definition |
| **ML part** | only the label instances the lookup did not give — the text layer's own job (precision, recall, F1 as before) |

## 3. How it works

For every flagged (piece, label) pair the model gets one question:

```
Risk label: Crime-Corruption
<definition, coverage and boundary from the taxonomy, column C>
Watchlist record field: Occupations
Text to judge: "Head of the Main Department for Combating Organised Crime and Corruption"
Surrounding text (context only): "<previous piece> [...] <next piece>"
Does the text to judge, read in its context, support assigning this label to the listed person or entity, …?
Answer yes or no.
```

* The answer is read as **P(yes)** from one model pass — no text is generated, so it is fast.
* A record gets a label if **any** of its flagged pieces scores ≥ 0.5.
* The **surrounding text** matters: in a first test, pieces from different records were placed side by side by mistake,
  and the model used another person's bribery sentence to answer yes. With the record's own context it answers no.
* Everything runs on the laptop's RTX 4070. **No data leaves the machine.**

## 4. Results (15 labels; the 5 not-covered and 3 lookup-only labels excluded)

| Approach | ML-part precision | ML-part recall | **ML-part F1** | Wrong labels added |
|---|---|---|---|---|
| TF-IDF + LR (notebook 01/02), new lists | 0.48 | 0.67 | 0.56 | 440 |
| Best whole-record model (notebook 02), new lists | 0.52 | 0.70 | 0.59 | 395 |
| Highlighter only (every flag = yes) | 0.48 | 0.95 | 0.63 | 636 |
| Highlighter + checker, question v1 | 0.71 | 0.87 | 0.78 | 220 |
| **Highlighter + checker, question v2** | **0.73** | **0.88** | **0.80** | **195** |

Question **v2** adds the taxonomy's sample indicators, "victims, officials fighting the conduct and list authorities do
not count", "one event can carry several labels", and a PEP note on non-state armed groups. It fixed the robbery → Theft
miss and the militia-leader → PEP error; it did **not** fix "Homicide Victim" → Violent Crime (the 4B model ignores that
instruction). v2 was written after seeing v1's errors, so its gain on these records is slightly optimistic.

### Reviewing the reference

Every one of the 239 cases where the checker and the reference disagree was reviewed against the taxonomy definitions
(`data/reference_review.csv`, one reason per case):

| Outcome | Cases | Examples |
|---|---|---|
| reference right, checker wrong | 176 | election candidates and the listing authority called PEP; Taliban roles called Terrorism; victims called Violent Crime |
| reference missed the label | 60 | Iranian nuclear-programme designations → Proliferation; "wanted on a burglary **charge**", probation, court hearings → Judicial; killing of civilians → Violent Crime alongside Human Rights |
| reference had a wrong label | 3 | "use of weapons of mass destruction" (US legal term for explosive attacks) → not Proliferation |

A random 40 cases where both said "yes" were also checked: all 40 correct, so hidden "both wrong" cases are rare.
Caveat: the review was done by Claude against the taxonomy, not by a human.

| Against the **reviewed** reference | Precision | Recall | F1 |
|---|---|---|---|
| Best whole-record model (notebook 02) | 0.54 | 0.66 | 0.60 |
| **Highlighter + checker v2** | **0.82** | **0.89** | **0.85** |
| …wanted lists | **0.92** | | |
| …sanctions & terror lists | 0.71 | | |

Per label: **Fraud 1.00, Corruption 0.94, Theft 0.94, Money Laundering 0.93** (≥ 90%); Cybercrime 0.89, Judicial 0.87,
Violent Crime 0.86 close; **PEP 0.59** and **Terrorism 0.52** far below.

### Examples

| Text | Label | Checker v2 | Reviewed reference | Comment |
|---|---|---|---|---|
| *Mitja Petrič is wanted on suspicion of money laundering and fraud.* | Fraud | yes | yes | correct |
| *…involvement in kidnapping, robbery and extortion* | Theft | yes | yes | fixed by v2 |
| *Secretary General and leader for the Coalition nationale…* (a militia coalition) | PEP | no | no | fixed by v2 |
| *Head of the Esfahan Nuclear Fuel Research and Production Center* | Proliferation | yes | yes (reference gap fixed) | checker right |
| *Homicide Victim, Lynchburg, Virginia* | Violent | yes | no | checker wrong: the person is the victim |
| *SENATOR of PHILIPPINES* (a 2025 candidate) | PEP | yes | no | checker wrong: candidates are not PEPs |

### Written reasons: not trustworthy with this model

Asked for a one-sentence reason, the model sometimes **invents** facts ("false promises of high returns" for a plain
"wanted for fraud" sentence) or **contradicts its own score** (score *no*, written answer *yes*). So the decision uses the
**score**, and the evidence shown to a reviewer is the **flagged sentence itself**, not the model's reason.

### Cost

0.15 s per question (v2) → ≈ **9 minutes per 1,000 records** on the laptop GPU; 300k records ≈ 47 h (≈ 27 h reusing
answers for identical pieces). On the laptop **CPU** the same model takes ≈ 24 s per question (≈ 25 h per 1,000 records),
so a CPU-only server needs a smaller, distilled model. Local, no per-call cost. GPU peak 76 °C.

## 5. Likely senior questions

**Is the checker just keyword matching?** No. The highlighter matches words; the checker reads the piece, its neighbours
and the label definition, and decides. Example: *"Combating Corruption"* → no; *"bribing scheme"* → yes.

**Why does it work on new lists when the trained models did not?** It was never trained on our lists, so it cannot learn
a list's writing style; it judges each short piece against the definition. That is exactly the failure of the
whole-record models (notebook 02).

**Is it bank-grade?** Not yet overall. Against the reviewed reference precision is 0.82; wanted lists (0.92) and four
labels already exceed 90%, but sanctions lists (0.71), PEP (0.59) and Terrorism (0.52) do not. Their mistakes are
systematic (candidates, list authorities, Taliban roles, victims), so they are fixable.

**Why not use the model's explanation as the audit trail?** This 4B model's written reasons can be invented. The flagged
sentence is factual and always available; that is the evidence.

**Could a bigger model do better?** Likely. The same questions can be sent to a larger local model (e.g. 8B in 4-bit
fits the laptop), a server GPU, or a cloud model (needs approval to send data out). The notebook is set up so only the
model changes.

## 6. Takeaways

1. Highlighter + checker gives precision **0.82** / recall **0.89** against the reviewed reference, vs 0.54 / 0.66
   for the best whole-record model, with no training on our labels — the design choice is confirmed.
2. Wanted lists already exceed 90% precision (0.92); sanctions lists (0.71), mainly PEP and Terrorism, are the gap.
3. Decisions come from the score, evidence from the flagged sentence; it runs fully on the laptop at ≈ 8 min per 1,000 records.

*All numbers in this file come from `04_checker.ipynb` outputs (`results/checker_score_card.csv`,
`results/checker_per_label.csv`, `results/checker_score_card_reviewed.csv`, `results/checker_per_label_reviewed.csv`),
`data/reference_review.csv` and the notebook 02 predictions it loads.*
