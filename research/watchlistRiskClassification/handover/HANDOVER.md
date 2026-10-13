# HANDOVER — PDG-84 watchlist risk classification research

This file lets a **new Claude Code session on another machine** (the RTX 4070 laptop) continue exactly where the
first session stopped. Read it top to bottom before doing anything.

---

## 0. Instructions for Claude (read first)

1. Read this whole file, then every file in `handover/agent_rules/` and `handover/claude_memory/`. They are the user's
   standing rules (copied from the original repo and from the previous session's memory). Follow them.
2. Most important rules, in short:
   * **Explain for full understanding** (top priority): plain answer → where it fits in the big picture → why, with
     alternatives and pros/cons → concrete example from our data → define every term → 2–3 takeaways the user can
     explain to their senior. The user is new to AML and growing to senior level. Long is fine if structured.
   * **Never edit `.xlsx` files.** Give from → to tables instead. Generated workbooks belong to the user.
   * **Show from → to for every Python change** with a one-line reason.
   * **Never commit or push without asking.** Conventional commits with scope `PDG-84`.
     **Never add a `Co-Authored-By` line or any AI attribution.** Remind the user to close Excel before git commands.
   * Minimal comments (one-line docstrings), no comments in data/config files. Avoid over-engineering.
   * For borderline labelling rules, **decide yourself** from the data and say why; don't hand the user A/B tables.
   * Deliverables are strictly source-grounded: no numbers or claims you have not measured or read.
3. Copy the important facts from this file into your own memory for this machine.
4. Then tell the user where things stand (section 9) and confirm the next step with them.

---

## 1. What this project is

* **Ticket:** PDG-84, "Research and Select the Best Approach and Model for Watchlist Member Risk Category Classification".
  **Watchlist only** (the user explicitly excluded adverse media).
* **Goal:** give every watchlist member the right risk labels out of **23** (taxonomy:
  `data/taxonomy/VV_Risk_Labels_Final_reviewed.xlsx`, sheet "Risk Labels", English definitions in column C,
  sample indicators in column E), **at the lowest cost**, with **bank-grade (AML) reliability**.
* **The user's top requirement:** the solution must **work on lists it has never seen** (new watchlists are onboarded regularly).
* **Origin:** repo `python-components` (VinnoVigilance), branch `feature/PDG-84-watchlist-risk-classification-research`
  (local only, **nothing committed or pushed**). This research folder is `research/watchlistRiskClassification/` in that repo.
  The folder you have is a **zip copy** of it; the rest of the repo is not needed for the experiments.
* Design discussion page (older, v2, watchlist-only): https://claude.ai/artifact/UsfT6T2bqX6iJzkQx7SxVX

### The 23 labels
Sanctions, PEP, RCA, Crime-Corruption, Crime-Fraud, Crime-Money Laundering, Crime-Trafficking & Smuggling,
Crime-Cybercrime, Crime-Violent Crime, Crime-Theft & Property Crime, Crime-Forgery & Counterfeiting, Crime-Tax Crime,
Crime-Securities & Market Crime, Crime-Organized Crime, Crime-Environmental & Wildlife Crime,
Terrorism / Terrorist Financing, Proliferation / Proliferation Financing, Human Rights / Atrocities, Regulatory,
Law Enforcement, Judicial / Court, Jurisdiction Risk, State-Owned. (Column order = `data/label_names.json`.)

---

## 2. The design (layers)

```
watchlist record
  ├─ Layer 1  LOOKUP (rules)   list / measure type / DMW licence status / programme / FBI category → labels
  ├─ Layer 2  TEXT LAYER       reads remarks, charges, narratives → labels the rules cannot give
  └─ FINAL = lookup ∪ text layer   (∪ = union)
```

* **Rule principle:** a code gives a label only if its official meaning *is* that conduct; otherwise "text decides".
* `scripts/lookupRules.py` is the **single source** of the decided rules (used by both the workbook and the labels).
  `lookups/watchlistRiskLookup.xlsx` is the human-readable copy (generated once; user-owned now; never regenerate/edit).
* The production database (server) can only be queried by the user pasting ONE query and exporting the result
  (`sql/lookupInventory.sql` → `data/serverInventory.csv`).

---

## 3. The data (know it)

| File | Content |
|---|---|
| `data/dataset_900.jsonl` | 900 records: `record_key, source (list), entity_type, part, text, labels` |
| `data/labels_900_binary.csv` | same labels as 23 columns of 0/1 |
| `data/lookup_labels_900.csv` | what the lookup rules alone give each record (23 columns of 0/1) — lets you run everything without the main repo |
| `data/label_names.json` | label column order |
| `data/disputed_decisions.csv` | 40 labeller disputes, decided with reasons |
| `data/label_corrections.csv`, `data/label_changes.csv` | manual fixes and the full change log |
| `data/taxonomy/VV_Risk_Labels_Final_reviewed.xlsx` | taxonomy with definitions + indicators |

* **How the labels were made:** two LLM labellers (Claude, GPT) → agreed labels; 40 disputes decided by rule;
  every lookup-rule label added; 1 manual correction. **One valid label set** (no Claude-vs-GPT columns).
  Labels are LLM-made, so a human-checked test set is still planned.
* **Sample parts:** A list coverage (364), **B rare-label top-up, keyword-picked (339, easier than reality)**,
  C hard cases (56), **D random (141, the only honest frequencies)**.
* **29 lists**, grouped into segments: Sanctions & terror lists (465 records), Wanted lists (210), Regulatory /
  debarment (114), Officials & candidates (77), Business register DNFBP (34).
* **Record text** = rendered lines: `Source list:`, `Entity type:`, `Programs:`, `Measures:`, `Titles:`, `Occupations:`,
  `Relationships:`, `Additional info:`, `Source note:`, `Remarks:`. Experiments use `text_noname` (the `Source list:`
  line removed — the list name is a shortcut that hurts new lists).
* **Three kinds of content:** (1) codes (programmes, measures) → rules; (2) short charge phrases
  (Interpol `TAX EVASION; MONEY LAUNDERING`, US Marshals `Original Charge: Tax Fraud`); (3) narrative sentences
  (FBI, UK, SECO, EU Most Wanted statements).

### Key facts measured
| Fact | Value |
|---|---|
| Label instances (record-label pairs) | 1,629 |
| …given by the lookup | **60%** (984) — Sanctions 99.8%, Regulatory 100%, Law Enforcement 92% |
| …that the text layer must find | **40%** (645; 610 on the 15 text-layer labels) |
| Records needing the text layer | 40% overall, 31% in random part D |
| Text-layer cases where a **trigger word** is present | **93%** (wanted lists 95%, sanctions 88%) |
| How often a trigger word alone is right | **64%** ("taxi"/"tax identification number" ≠ Tax Crime; "ministry of armed forces" ≠ Violent; "forge and press plant" ≠ Forgery; "candidate for senator" ≠ PEP) |
| Interpol records with non-English charges | only 7 of 69 (language is **not** the main new-list problem; list format/vocabulary is) |
| Records > 320 tokens (cut off in CPU fine-tuning) | 127 (14%) |

**Summary: the data is keyword-triggered but context-decided.**

### Label counts (positives in the 900 / positives the text layer must find)
| Label | All | Text | Label | All | Text |
|---|---|---|---|---|---|
| Sanctions | 464 | 1 | Organized Crime | 45 | 37 |
| Law Enforcement | 204 | 17 | Cybercrime | 42 | 6 |
| PEP | 135 | 67 | Forgery | 34 | 34 |
| Violent Crime | 102 | 95 | Human Rights | 34 | 23 |
| Regulatory | 99 | 0 | Proliferation | 28 | 17 |
| Judicial | 89 | 89 | Corruption | 25 | 17 |
| Terrorism | 80 | 12 | Tax Crime | 17 | 17 |
| Fraud | 56 | 54 | Environmental | 7 | 7 |
| Theft | 53 | 53 | Securities | 5 | 5 |
| Money Laundering | 53 | 53 | State-Owned / RCA / Jurisdiction | 4 / 1 / 0 | same |
| Trafficking | 52 | 36 | | | |

---

## 4. How results are measured (same in every notebook, `scripts/experimentKit.py`)

* **Two protocols:**
  * **seen lists** — 5-fold multi-label stratified CV (`MultilabelStratifiedKFold`, seed 42);
  * **unseen lists** — 5 **list-grouped** folds (each list entirely in one test fold; balanced on text-layer positives).
    **This is the headline** (the user's top requirement).
* Per-label thresholds are **cross-fitted** (chosen on the other folds' out-of-fold scores).
* **Score views:** whole label set (lookup ∪ model, 23 labels: P / R / micro-F1, macro-F1 with and without the 5 rare
  labels), text-layer macro-F1, **ML part** (only instances the lookup did not give: recall = found / missed-by-lookup,
  precision = correct / labels the model added), part D, macro PR-AUC, per label (with 95% Wilson intervals), per segment.
* Labels with < 15 positives are not modelled (RCA, State-Owned, Securities, Environmental, Jurisdiction Risk).

---

## 5. Results so far

### Notebook 01 — EDA + traditional ML (`notebooks/01_eda_traditional_ml.ipynb`, explained in `notebooks/01_eda_traditional_ml_explained.md`)
* Best: **char TF-IDF (2–5) + logistic regression, class-balanced, no list name (M8)**.
* Whole set (23 labels): P 0.92, R 0.88, micro-F1 0.90; macro-F1 0.67 with rare, 0.85 without.
* **ML part:** recall 0.71, precision 0.79 (recall 0.73 over the 18 modelled labels).
* Leave-one-list-out: ML recall 0.74 (seen) → **0.48 (unseen)**. Removing the list name: free in-distribution, +0.05 unseen.
* Weak labels: Theft 0.67, Tax 0.63, Organized Crime 0.74, Forgery 0.75. One model per segment was worse than one global model.

### Notebook 02 — encoders (`notebooks/02_encoders.ipynb`; source `notebooks/source/02_encoders.py`)
Interim score card: `results/encoders_interim_card.csv`. ML-part F1:

| Approach | Seen lists | **Unseen lists** |
|---|---|---|
| B0 char TF-IDF + LR (C tuned) | **0.761** | 0.498 |
| E0 MiniLM-L6 (English) + LR | 0.678 | 0.454 |
| E1 multilingual-e5-small + LR | 0.685 | 0.460 |
| E2 multilingual-e5-base + LR | 0.682 | 0.477 |
| E3 bge-m3 + LR | 0.721 | 0.477 |
| Z zero-shot (similarity to label definitions) | 0.303 | 0.261 |
| H MiniLM + definition similarities + LR | 0.686 | 0.431 |
| **ENS B0 + e5-base** | 0.756 | **0.551 (best so far)** |
| FT multilingual-e5-small fine-tuned (CPU: lr 8e-5, 6 epochs, 320 tokens) | 0.669 | *in progress / to finish* |
| ENS B0 + FT | 0.765 | *to finish* |

* Frozen encoders do **not** beat TF-IDF (one summary vector blurs the decisive phrase; only 900 records).
* Fine-tuning on CPU was worse than TF-IDF on seen lists (720 training records for 118M weights; 14% of records
  truncated; tuning curve still rising at epoch 6; one seed).
* Zero-shot is useful only as a **shortlist for rare labels**: all 5 Securities positives in the top 50 of 900;
  Environmental 4 of 7 in the top 50 (ranks 1 and 2); not State-Owned or RCA.
* Encoding speed (CPU, per 1,000 records): MiniLM 131 s, e5-small 49 s, e5-base 358 s.

---

## 6. Decisions made with the user

1. Watchlist only; one valid label set; 0/1 columns kept; lookup independent of counts.
2. Lookup rules decided from data (Claude decides borderline rules); training labels follow them.
3. Single server query for server values; never modify `data/rules/riskClassification.xlsx` in the repo.
4. Model selection is driven by **unseen-list** performance.
5. **Proposed label tiers** (agreed in principle; still to apply in the score card — Part A4 below):
   * Lookup only: Sanctions, Regulatory, Law Enforcement.
   * **A validated ML (≥ 50 text positives):** Violent Crime, Judicial, PEP, Fraud, Money Laundering, Theft.
   * **B provisional ML (30–49):** Organized Crime, Trafficking, Forgery — model output = suggestion, needs review/LLM.
   * **C no ML (< 30):** Human Rights, Proliferation, Corruption, Tax, Terrorism (text part), Cybercrime (text part),
     Environmental, Securities, State-Owned, RCA, Jurisdiction Risk → rules / LLM / review; promote when ≥ 50 positives.
   * Reason: with n positives, even a perfect model only proves recall ≥ n/(n+3.84) at 95% (15 → 0.80, 30 → 0.89, 50 → 0.93).
   * Never drop a label from the product — only remove ML responsibility for it.
6. **New direction (user approved):** classify **evidence units**, not whole records:

```
record → L1 lookup (codes)
       → L2 split into units (charge phrases, sentences; drop boilerplate, IDs, addresses)
       → L3 candidates: list-agnostic trigger-word lexicon (taxonomy indicators + conduct words, whole-word rules)
       → L4 decide in context per (unit, label): LLM with label definitions now; small fine-tuned model
            on (unit, definition) pairs later (distilled from LLM + human answers) — good use of the RTX 4070
       → L5 cache distinct units   → L6 route low confidence to LLM / human review
```
   Why: whole-record models learn each list's style (fails on new lists); units carry no list style; keywords give
   recall (93%), context gives precision; every label gets an evidence sentence (auditability); the LLM reads only
   short candidate units (fixes the earlier 67 s/record = months problem).

---

## 7. Folder map

| Path | What |
|---|---|
| `HANDOVER.md` | this file |
| `README.md` | layout + rebuild commands (rebuild scripts need the full repo; experiments do not) |
| `requirements-research.txt` | pinned packages (torch line is the CPU build there; on the GPU laptop install the CUDA build) |
| `scripts/experimentKit.py` | data loading, fold protocols, score views — **use it for every new notebook** |
| `scripts/lookupRules.py` | decided lookup rules |
| `scripts/buildDataset.py`, `buildLookupWorkbook.py`, `measureLookupCoverage.py`, `watchlistText.py` | builders (need the full repo's `data/final` and `data/reports`; not needed for experiments) |
| `notebooks/01_eda_traditional_ml.ipynb` + `_explained.md` | step 4a |
| `notebooks/02_encoders.ipynb` | step 4b (may be missing/old — regenerate, see section 8) |
| `notebooks/source/*.py` | notebook sources in percent format (`# %%` cells); `runNotebook.py` builds + executes them; `02_interim_card.py`; `batteryGuard.ps1` (old laptop only) |
| `cache/` | embeddings, label-definition embeddings, `ft_tuning.json`, `ft_{seen,unseen}_fold{k}.npy` (fine-tune fold scores), run logs |
| `results/` | CSV outputs of every notebook |
| `lookups/watchlistRiskLookup.xlsx` | user-owned lookup workbook (do not edit) |
| `handover/agent_rules/`, `handover/claude_memory/` | the user's rules and the previous session's memory |

---

## 8. Setting up the RTX 4070 laptop

1. Unzip this folder anywhere (e.g. `C:\work\watchlistRiskClassification`). **Cloning the repo is not needed.**
2. Install **Python 3.12** (64-bit). Check the GPU driver: `nvidia-smi` must work; note its "CUDA Version".
3. Create the environment (PowerShell, inside the folder):
   ```
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
   python -m pip install -r requirements-research.txt
   python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
   ```
   Use the CUDA index that matches the driver (`cu126`, `cu128`, …; see pytorch.org "Get Started").
   The last line must print `True` and the GPU name.
4. Models download from Hugging Face on first use (multilingual-e5-small ~0.5 GB; bge-m3 ~2.3 GB). On Windows set
   `HF_HUB_DISABLE_SYMLINKS_WARNING=1` to silence a harmless warning.
5. Finish notebook 02 (the code uses the GPU automatically; cached steps are skipped, missing fine-tune folds are trained):
   ```
   cd notebooks
   ..\.venv\Scripts\python.exe source\runNotebook.py source\02_encoders.py 02_encoders.ipynb
   ```
   Note: folds already in `cache/` were trained on CPU; new folds on GPU can differ slightly (non-determinism).
   To retrain everything on GPU (recommended for a clean comparison, minutes on a 4070), delete `cache/ft_*_fold*.npy`
   first. Keep `cache/ft_tuning.json` or delete it to re-tune.
6. Open the folder in Claude Code and start with: *"Read HANDOVER.md and everything in handover/, then tell me where
   we are and what's next."*

---

## 9. Where we stopped and what is next

**State at handover:** notebook 02's fine-tuning had finished 8 of 10 folds on the old laptop's CPU
(all 5 seen folds, unseen folds 0–2). The notebook's analysis text (`READING_*` and `CONCLUSIONS` placeholders in
`notebooks/source/02_encoders.py`) is **not written yet**.

### Part A — close the encoder step
1. Run notebook 02 to completion (section 8.5).
2. Fill the `READING_*` / `CONCLUSIONS` placeholders from the real outputs (markdown cells; the user wants analysed,
   explained outputs, not raw dumps), re-run.
3. Write `notebooks/02_encoders_explained.md` in the style of the 01 explainer (methods, results, senior Q&A, takeaways).
4. Add the label-tier view (section 6.5) to the score card in `experimentKit.py`.
5. Optional, now cheap on GPU: fine-tune with 512 tokens, 10+ epochs, 3 seeds; bigger multilingual encoders
   (mDeBERTa-v3-base, XLM-R-base, mmBERT). Judge only on **unseen lists**.

### Part B — the "highlighter" (new notebook 03)
1. Split records into evidence units (charge phrases split at `;`, sentences; drop boilerplate = sentences repeated
   across a list, IDs, addresses). Report units per record and distinct units.
2. Trigger lexicon per label from the taxonomy "Sample Indicators" + conduct words (+ few non-English), whole-word rules;
   any word learned from data only from training lists (no leakage).
3. Measure on **unseen lists**: candidate recall per label (goal ≈ 100%), units flagged per record (LLM workload),
   flag precision; inspect the ~7% of cases with no trigger word.
4. Build a small sentence-level answer sheet (unit, label, yes/no) for the user to spot-check → test set for the LLM step.
   Estimate cost for ~300k records (distinct units × time per unit).

### Part C — LLM decider (needs user decisions)
* Where to run it: the RTX 4070 (local model, data stays local), the company server, or a cloud API (needs key + data OK).
* LLM reads each candidate unit with the label definition → yes/no + short reason; scored with the same score card on unseen lists.
* Later: distil into a small fine-tuned model on (unit, definition) pairs on the 4070.

### Later roadmap items
Human-checked test set (~400 items, over-sample weak labels and wanted lists); agreed targets before testing
(e.g. ML-part recall on unseen lists); label review for records without textual evidence; rare labels via rules
(Jurisdiction = country lookup; State-Owned / RCA = relationship rules) and the zero-shot shortlist.

---

## 10. Gotchas learned

* Old laptop: `files.pythonhosted.org` did not resolve for Claude's shell (pip failed); `download.pytorch.org` and
  Hugging Face worked. The user installed packages from their own shell.
* `runNotebook.py` writes the notebook only when all cells finish; outputs are not visible mid-run. Heavy steps are cached.
* Windows: `tail -f` locks files (blocked log writes and process restarts) — poll with `cat` instead.
* Windows venv `python.exe` is a launcher: each Python process appears twice in process lists.
* Laptop sleep freezes background runs; keep the machine awake during long runs.
* Fine-tuning on CPU ≈ 23 min per fold (e5-small, 6 epochs, 320 tokens); on a 4070 expect ~1 min.
