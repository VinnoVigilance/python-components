# PDG-84 — Watchlist risk category classification research

Research for choosing the approach and model that assigns the 23 VV risk labels
(`data/taxonomy/VV_Risk_Labels_Final_reviewed.xlsx`) to watchlist members, at low cost, with bank-grade precision,
and working on lists the system has never seen.

## Outcome

```
record ─► lookup rules (list / measure / programme / FBI category)      → Sanctions, Regulatory, Law Enforcement, list rules (DILG, COMELEC → PEP)
       ─► highlighter (pieces + trigger words, scripts/highlighter.py)  → candidate pieces
       ─► checker = teacher (Qwen3-4B / Qwen3-8B, GPU, scripts/checker.py) → yes/no per (piece, label), offline
       ─► student = multilingual-e5-small fine-tuned on the teacher (CPU) → production model, cache/student_e5small_v6
       ─► policy: auto-apply or review queue, each label with its evidence sentence
```

* Scope: 3 lookup-only labels + **15 text-layer labels**; RCA, State-Owned, Securities, Environmental and Jurisdiction
  Risk are out of scope (too few examples).
* Proof (v5, 701 untouched records, blind labels): precision Human Rights 0.98, Proliferation 0.95, Violent Crime 0.94,
  Terrorism 0.94, Judicial 0.92, PEP 0.92, Theft 0.90, Organized Crime 0.87, Trafficking 0.78, Fraud 0.75.
  Held-out lists: EU-MOST-WANTED 0.92 / 0.88, DFAT 0.96 / 0.66, DILG 1.00 / 1.00 (precision / recall).
* **Auto-apply policy (decided 2026-10-13):** measured precision ≥ 0.90, at least 50 checks and Wilson 95% lower bound
  ≥ 0.85 → Human Rights, Violent Crime, Terrorism, Proliferation, Judicial, PEP. All other text-layer labels go to the
  review queue and are promoted from logged reviewer decisions.
* Student on CPU: about 30 ms per piece, about 1.5 hours per 100,000 records.
* v6 (Fraud, Organized Crime, PEP fixes) is frozen but not yet proven: it needs a new, untouched server export.

Full story and numbers: `handover/SESSION_2026-10-12_RTX4070_part2.md`. Plain-language walkthroughs:
`notebooks/0*_explained.md`.

## Start here

1. This README, then `handover/SESSION_2026-10-12_RTX4070_part2.md` (full final story and decisions).
2. `notebooks/01`–`04_*_explained.md` — each step in plain language.
3. `results/v5_precision_by_label.csv` — the headline proof; `results/frozen_versions.json` — exact settings.
4. `data/labelling_rules_pdg84.md` — what each label means in practice.
5. `scripts/lookupRules.py`, `scripts/highlighter.py`, `scripts/student.py` — the pieces production reuses.

The student model is stored with Git LFS: run `git lfs install` once before cloning or pulling.

## Layout

| Path | Content |
|---|---|
| `data/dataset_900.jsonl`, `data/labels_900_binary.csv`, `data/label_names.json` | The 900 dev records with final labels (text + 0/1 columns) |
| `data/disputed_decisions.csv`, `data/label_corrections.csv`, `data/label_changes.csv` | Decided disputes, manual fixes, full change log |
| `data/lookup_labels_900.csv` | What the lookup rules alone give each record |
| `data/labelling/` | Original labelling: 900 sample, Claude-vs-GPT verdict workbook, Claude review verdicts (314), 3-way adjudication of the 40 disputes, final gold labels, labelling guide and the scripts that built them |
| `data/labelling_rules_pdg84.md`, `data/blind_labelling_instructions.md` | Labelling rules 1–10 and the blind review protocol |
| `data/reference_review.csv` | Reviewed reference for the dev 900 (239 disagreements) |
| `data/dev_blind_labels*.csv`, `data/fresh_blind_labels*.csv` | Blind labels (v2 = after the PEP rule change) for the dev check and the fresh test sets |
| `data/taxonomy/`, `data/serverInventory.csv` | Taxonomy copy; server inventory of lists / measures / programmes |
| `lookups/watchlistRiskLookup.xlsx` | Human-readable lookup tables (user-owned, do not regenerate) |
| `scripts/lookupRules.py` | Decided lookup rules (single source) |
| `scripts/highlighter.py`, `scripts/checker.py`, `scripts/student.py` | Production candidates: highlighter, LLM checker (teacher), CPU student |
| `scripts/experimentKit.py` | Data loading, fold protocols (seen / list-grouped unseen), score views, label tiers |
| `scripts/` (others) | Dataset / workbook builders, fresh split, check pools, runs, scoring, freezes |
| `notebooks/01`–`04` (+ `_explained.md`, `source/`) | EDA + traditional ML, encoders, highlighter, checker |
| `results/` | Every result table; `frozen_versions.json` pins prompt, rules and file hashes per version |
| `sql/` | Server queries: lookup inventory, teacher sample export |
| `handover/` | Handover to the GPU laptop and its two session reports |
| `cache/student_e5small_v6/` | The trained student model (weights in Git LFS); the rest of `cache/` is git-ignored |

## How final labels are built

1. Base: labels both labellers gave, else labels both review verdicts chose, else `disputed_decisions.csv`.
2. Every label the lookup rules give the record is added (training labels always follow the lookup rules).
3. `label_corrections.csv` is applied.

## Rebuild and run

```bash
./vv-env/Scripts/python.exe research/watchlistRiskClassification/scripts/buildDataset.py
cd research/watchlistRiskClassification/scripts && ../../../vv-env/Scripts/python.exe measureLookupCoverage.py
```

`buildDataset.py` reads `data/labelling/` and the repo's `data/final/`. `buildLookupWorkbook.py` refuses to overwrite
the existing workbook. Model work uses `research/watchlistRiskClassification/.venv` (git-ignored) with
`requirements-research.txt`; the checker and student training need a CUDA GPU, the student runs on CPU.
Not stored here: the server export, teacher answers and blind-labelling batches (personal data, kept on the GPU laptop).
