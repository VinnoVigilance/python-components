# Notebook 02 explained — pre-trained encoders

Companion to `02_encoders.ipynb`. What was done, how, why, the results, and likely senior questions.

## 1. The big picture

```
watchlist record
      │
      ├─► Layer 1: LOOKUP (rules)      codes → labels               gives 60% of label instances
      │
      ├─► Layer 2: TEXT MODEL          reads the text → labels      must find the other 40%
      │        notebook 01: char TF-IDF + logistic regression (baseline)
      │        notebook 02: pre-trained encoders  ← this notebook
      │
      └─► FINAL LABELS = lookup ∪ model
```

Notebook 01 found the deciding weakness: the TF-IDF model works on lists it was trained on but drops sharply on a
**new list** (new lists are onboarded regularly). Notebook 02 asks: do pre-trained language models (encoders), which
already "know" language, close that gap?

## 2. Terms used

| Term | Plain meaning |
|---|---|
| **Encoder** | a neural network pre-trained on huge amounts of text that turns a text into a vector (a list of 384–1,024 numbers); texts with similar meaning get similar vectors, even across languages (*"Asesinato"* ≈ *"murder"*) |
| **Frozen embeddings** | use the encoder as-is: encode every record once, then train a simple logistic regression per label on the vectors |
| **Fine-tuning** | adjust all the encoder's weights (118 million here) to our labels |
| **Zero-shot** | no training: compare each record's vector with the vector of the label's *definition* from the taxonomy |
| **Ensemble** | average the probabilities of two models |
| **Multilingual** | trained on ~100 languages (our data has English, German SECO, Spanish/French/Portuguese Interpol) |
| **Token** | a word piece the encoder reads; encoders read at most 512 (bge-m3: 8,000) |
| **Seen / unseen lists** | test records come from lists that were / were not in training (see section 3) |
| **ML part** | only the label instances the lookup did *not* give — the model's own job. Precision = share of labels the model *added* that are right; recall = share of the lookup's misses the model found |
| **PR-AUC** | how well a model ranks true cases above false ones, independent of the cut-off |

## 3. How it was tested (same as every notebook, `scripts/experimentKit.py`)

* **Seen lists:** 5-fold stratified cross-validation — every list appears in training and test. Same folds as notebook 01.
* **Unseen lists (headline):** 5 folds where **each list is entirely in one test fold**. Example: fold 1 tests
  Interpol and OFAC-SDN with a model that has never seen either. This simulates onboarding a new list.
* Thresholds per label are chosen on the other folds (cross-fitted), so no record sets its own cut-off.

## 4. What was tried

| # | Approach | Model | Why it was in the test |
|---|---|---|---|
| B0 | char TF-IDF + LR | — | the baseline from notebook 01 |
| E0 | frozen + LR | MiniLM-L6 (22M, English only) | control: does multilingual matter? |
| E1 | frozen + LR | multilingual-e5-small (118M) | standard small multilingual model |
| E2 | frozen + LR | multilingual-e5-base (278M) | does size help? |
| E3 | frozen + LR | bge-m3 (568M, long input) | strongest common open multilingual embedder |
| Z | zero-shot | best encoder vs label definitions | works without examples — rare labels, new lists |
| H | hybrid | MiniLM + the 23 definition similarities + LR | definitions as prior knowledge |
| ENS | ensembles | B0 averaged with E2 / H / FT | combine "letters" and "meaning" |
| FT | fine-tuned | multilingual-e5-small, 6 epochs, 320 tokens, lr 8e-5 | usually the strongest when data is enough |

Fine-tuning ran on the **RTX 4070** laptop: ~6 s per epoch, 424 s for all 10 folds (vs ~23 min per fold on the old
CPU). The seen-list result matched the earlier CPU run (0.668 vs 0.669).

## 5. Results

Headline = **ML-part F1 on unseen lists**.

| Approach | ML-part F1 seen | **ML-part F1 unseen** | ML-part P unseen | ML-part R unseen |
|---|---|---|---|---|
| **ENS TF-IDF + e5-base** | 0.756 | **0.551** | 0.455 | 0.697 |
| ENS TF-IDF + fine-tuned | **0.774** | 0.504 | 0.402 | 0.678 |
| TF-IDF + LR (baseline) | 0.761 | 0.498 | 0.393 | 0.680 |
| ENS TF-IDF + hybrid | 0.762 | 0.488 | 0.388 | 0.658 |
| e5-base + LR | 0.682 | 0.477 | 0.378 | 0.646 |
| bge-m3 + LR | 0.721 | 0.477 | 0.382 | 0.634 |
| e5-small + LR | 0.685 | 0.460 | 0.358 | 0.643 |
| MiniLM + LR | 0.678 | 0.454 | 0.380 | 0.564 |
| hybrid | 0.686 | 0.431 | 0.349 | 0.565 |
| fine-tuned e5-small | 0.668 | 0.412 | 0.309 | 0.618 |
| zero-shot | 0.303 | 0.261 | 0.171 | 0.546 |

Whole label set (lookup included, what a customer sees), micro-F1: TF-IDF 0.905 seen / 0.763 unseen; best ensemble 0.901 / 0.795.

### What the numbers say

1. **No encoder alone beats TF-IDF** — frozen or fine-tuned, small or large.
2. **Averaging TF-IDF with e5-base is the best on new lists** (+0.053), mostly through precision (0.393 → 0.455).
3. **The new-list problem is precision.** Seen → unseen, TF-IDF precision falls 0.806 → 0.393 while recall only
   falls 0.721 → 0.680. The models still find the right area, but on a new list more than half of what they add is wrong.
4. **Multilingual helps a little, size does not:** unseen recall MiniLM (English) 0.564 vs multilingual 0.634–0.646;
   e5-base and the twice-as-large bge-m3 tie (0.477).
5. **Fine-tuning is the weakest trained approach on new lists** (0.412). Combined with TF-IDF it is the best on *seen*
   lists (0.774) — what it learns is mostly the training lists' patterns.

### Per label (best ensemble vs TF-IDF, unseen lists)

* Better: Tax Crime F1 0.13 → 0.45, Terrorism 0.81 → 0.95, Trafficking 0.70 → 0.81, Fraud 0.79 → 0.83, Forgery 0.48 → 0.56.
* Worse: Proliferation 0.85 → 0.70, Theft 0.47 → 0.38, Organized Crime 0.67 → 0.63, Corruption 0.63 → 0.59.
* Weakest: Theft 0.38, Tax 0.45, Forgery 0.56 — the lookup never gives them and their trigger words are ambiguous.
* **The model damages lookup-solved labels on new lists:** Sanctions is 99.8% given by the lookup, but its precision on
  unseen lists is 0.859 because the model *adds* wrong Sanctions labels; Regulatory 0.805, Law Enforcement 0.870.

### Zero-shot for rare labels

| Rare label | Positives | Ranks of the true positives among 900 | In top 50 |
|---|---|---|---|
| Securities | 5 | 5, 15, 18, 36, 44 | 5 of 5 |
| Environmental | 7 | 1, 2, 31, 49, 256, 529, 663 | 4 of 7 |
| State-Owned | 4 | 53, 62, 94, 454 | 0 |
| RCA | 1 | 481 | 0 |

Useful as a review shortlist for Securities and Environmental only.

### Label tiers (section 8b of the notebook)

Rule, computed from the data (a label moves up automatically when it gets more examples):

| Tier | Rule | Labels | Model output |
|---|---|---|---|
| lookup only | lookup gives ≥ 90% of positives | Sanctions, Regulatory, Law Enforcement | dropped |
| A validated ML | ≥ 50 positives the lookup misses | PEP, Fraud, Money Laundering, Violent Crime, Theft, Judicial | used |
| B provisional ML | 30–49 | Trafficking, Forgery, Organized Crime | suggestion, needs review / LLM |
| C no ML | < 30 | the other 11 | dropped → rules / LLM / review |

Why 50: with n true cases, even a perfect model only proves recall ≥ n / (n + 3.84) at 95% confidence (30 → 0.89, 50 → 0.93).

Unseen lists:

| | TF-IDF | Best ensemble |
|---|---|---|
| Lookup-only labels the model added / of which wrong | 236 / **220** | 143 / **130** |
| Tier A ML-part F1 | 0.599 | 0.610 |
| Tier B ML-part F1 | 0.567 | 0.621 |
| Tier C ML-part F1 | 0.378 | 0.459 |
| Whole micro-F1 as is → with tiers | 0.763 → **0.811** | 0.795 → **0.819** |
| Whole precision as is → with tiers | 0.681 → 0.795 | 0.730 → 0.806 |

* On new lists, the model's additions to the three lookup-only labels are ~93% wrong — a third of all its wrong
  additions (TF-IDF 220 of 660). Tiers remove them.
* ML-part recall drops (best: 0.697 → 0.592) because tier-C text cases (109) move to another step — not lost.
* On seen lists tiers cost a little (micro-F1 0.905 → 0.887); we choose for new lists.

### Cost (encoding, old laptop CPU, per 1,000 records)

TF-IDF < 1 s · e5-small 49 s · MiniLM 131 s · e5-base 358 s · bge-m3 3,794 s.
The best ensemble needs e5-base: ≈ 30 hours of CPU for 300k records at that speed (GPU not measured).

### Scope of the scores

The headline ML-part scores cover the **18 labels with at least 15 examples**. Five labels are **not covered yet**:
RCA (1 example), State-Owned (4), Securities (5), Environmental (7), Jurisdiction Risk (0). They have too few examples
to build or measure anything, and no lookup rule gives them. They stay in the taxonomy as future labels; only the
"incl. rare" columns count them (as misses).

## 6. Why encoders did not win here

* **One vector per record blurs the deciding phrase.** In a long Interpol record, "TAX EVASION" is one phrase among
  many lines; averaged into one vector it barely moves it. TF-IDF keeps every phrase as its own feature.
* **900 records is small** for learning on 384–1,024 dense numbers, and very small for adjusting 118M weights.
* **Context, not vocabulary, decides our labels.** Notebook 01's data facts: a trigger word is present in 93% of cases
  the text layer must find, but the word alone is right only 64% of the time ("taxi", "candidate for senator").
  Whole-record models cannot see *which* sentence caused a label, so on a new list they misjudge context → low precision.

## 7. Likely senior questions

**Encoders are state of the art — why does TF-IDF win?** State of the art for meaning-similarity, not for "spot the one
decisive phrase in a long record with 900 examples". The test was fair: same folds, same thresholds, same score views.

**Is fine-tuning really bad, or was it badly configured?** Partly open. Limits: 320-token cut (128 records truncated),
the tuning curve was still rising at epoch 6, one seed. Those are now cheap to test on the GPU. But it starts 0.09 below
TF-IDF on unseen lists, so small tuning gains are unlikely to change the conclusion.

**Why is precision the problem, not recall?** The models still find most labels (recall ~0.6–0.7) but add many wrong
ones on new lists. An analyst would see many false alerts — and the lookup-only labels (Sanctions, Regulatory,
Law Enforcement) get polluted.

**So what do we use?** As a whole-record model, TF-IDF + e5-base (0.551 unseen). But no whole-record model is close to
bank-grade on new lists, which is why the next step changes the unit of classification (section 8).

**Which labels should the model not predict?** Sanctions, Regulatory, Law Enforcement: the lookup gives them almost
perfectly and the model only adds false positives → "lookup only" tier.

## 8. What this means for the next step

The results back the evidence-unit design agreed earlier:

```
record → lookup (codes)
       → split into units (charge phrases, sentences)
       → trigger words flag candidate units      (finds the cases: recall)
       → a context reader decides each (unit, label)   (judges context: precision)
```

Short units carry no list style, so they should transfer to new lists; each label also gets an evidence sentence.
Notebook 03 measures the first half (units + trigger words) on unseen lists.

## 9. Takeaways

1. Pre-trained encoders do not beat char TF-IDF on our data; averaging TF-IDF with multilingual-e5-base is the best
   whole-record model on new lists (ML-part F1 0.551 vs 0.498).
2. The new-list weakness is **precision** (31–46% of added labels correct) — models find the area but misjudge context.
3. Label tiers help immediately: on new lists, the model's Sanctions / Regulatory / Law Enforcement additions are ~93%
   wrong; with tiers the best whole-set micro-F1 on new lists rises 0.795 → 0.819.
4. That is why we move from classifying whole records to classifying short evidence units.

*All numbers in this file come from `02_encoders.ipynb` outputs (`results/encoders_score_card.csv`,
`results/encoders_best_per_label.csv`, `results/encoders_tiers.csv`) and, for the 93% / 64% trigger-word facts, from `HANDOVER.md` section 3.*
