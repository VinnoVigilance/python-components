# Media Risk Category Classification

Terminal-based experiments on 11 GPT/Claude positive-intersection labels. The selected method remains `char_svm_oof_thresholds`: character TF-IDF + balanced Linear SVM, with per-label thresholds learned from Train out-of-fold predictions.

## Update and run

Replace matching source and data files in your existing `research/mediaRiskCategoryClassification` folder with this archive. Keep your existing environment and `outputs/` folder. This archive contains no trained weights or run reports and does not delete historical output folders.

The active scope changed from 14 to 11 labels on 2026-10-19. Removed targets: `PEP`, `Crime-Organized Crime`, and `Crime-Theft & Property Crime`. They are absent from training targets, active question definitions, score columns and prediction reports. No article was deleted or reassigned to another split.

From the project folder in your existing environment:

```powershell
python run.py --model char_svm_oof_thresholds --threads 4
python run.py --compare
```

A new 11-label training run is required. Historical 14-label bundles belong to a different scope and cannot be evaluated by this version. The comparison regenerates its CSV index from completed runs matching the current dataset, protocol and label order; historical run folders remain unchanged. Before the first new run, comparison correctly reports no matching results.

For a new classical-model environment only:

```powershell
python -m pip install -r requirements.txt
```

## Active labels

1. Terrorism / Terrorist Financing
2. Regulatory
3. Law Enforcement
4. Judicial / Court
5. Crime-Corruption
6. Crime-Fraud
7. Crime-Forgery & Counterfeiting
8. Crime-Cybercrime
9. Crime-Trafficking & Smuggling
10. Crime-Violent Crime
11. Crime-Environmental & Wildlife Crime

An omitted label is outside this version's supported scope, not a finding that the risk is absent. In particular, do not interpret missing PEP output as a negative PEP assessment.

## Data and evaluation

Dataset version: `vv-media-intersection-11-v1`. Train: 3,396 articles; Validation: 728; Test: 727. All original article IDs, titles, bodies, model input text, body hashes, ordering, retained target values and split assignments are preserved. `split_manifest.csv` is byte-for-byte unchanged. The manifest records the parent dataset and removed targets.

Input is only `text`. Targets are the 11 binary columns in `config.py`. A positive means present in the GPT/Claude positive intersection; zero means absent from that intersection. Rows with no remaining positive label are retained: Train 1,806; Validation 395; Test 387. `selected_labels`, label counts and distributions reflect only the active scope.

Reference labels have not been independently human-adjudicated. Exact normalized body duplicates were grouped; semantic/event/source/time separation has not been established. The historical Test has already been inspected and informed this scope decision. Scores on that same Test are descriptive results, not a fresh independent final evaluation. Use a new held-out sample for final claims after further development.

Scope reduction changes which tasks are measured; it does not improve the unchanged classifiers for retained labels. The earlier 14-label scores must not be presented as directly comparable model improvements to 11-label averages.

## Outputs

Each run has a unique directory under `outputs/`, containing:

- `run.json` and `model.joblib`: scope, parameters, environment, hashes and fitted model.
- `validation_summary.json`: macro/micro Precision, Recall, F1 and other metrics.
- `validation_per_label.csv`: support, TP, FP, FN and metrics for each active label.
- `validation_predictions.csv` and `validation_errors.csv`: news, reference tags, predictions, missing/extra tags and 11 score columns.
- OOF models also save `threshold_tuning.json`, `threshold_tuning_per_label.csv` and `train_oof_scores.csv`.

`python run.py --compare` shows the latest matching completed run per model, sorted by Validation Macro-F1. `outputs/model_comparison.csv` contains only matching history and includes the label count. CPU timings should be compared on the same machine.

Evaluate a new saved run without fitting:

```powershell
python evaluate.py --run outputs/NEW_11_LABEL_RUN --split validation
```

`--split test` is also available, but it does not create a new independent test set. Thresholds are loaded from the saved run and are never retuned during evaluation. SVM margins are decision scores, not calibrated probabilities.

## Models and extension

Available modules retain their existing names: `char_svm_oof_thresholds`, `char_tfidf_svm`, `tfidf_svm`, `word_char_svm`, `tfidf_lr`, `char_tfidf_lr`, `char_svm_unweighted`, `tfidf_cnb`, `lsa_lr`, `minilm_lr`, `e5_small_lr`, `char_minilm_stack_oof`, and the three Laya configurations below. `python run.py` opens the model menu.

To add a model, copy `models/_template.py` to a public module and implement `build(seed)`. Return an unfitted estimator with `fit(texts, y)` and either `decision_function` or `predict_proba`, producing N-by-`len(config.LABELS)` scores. Keep fitting and threshold selection inside Train. The shared runner owns data loading and evaluation.

The main files are `config.py`, `data.py`, `evaluation.py`, `run.py`, `evaluate.py`, `models/`, `data/`, `tests/` and `outputs/`. There is one English Markdown document: this README.

## Optional frozen semantic models

Classical SVM/LR experiments do not need torch or Hugging Face. Keep an existing working semantic environment. For a fresh dedicated CPU environment, install CPU PyTorch and `requirements-embeddings.txt`; do not change an unrelated environment with conflicting dependencies.

MiniLM/E5 use a frozen encoder over all article windows followed by Logistic Regression. They are different from a fine-tuned encoder. Stacking uses fresh lexical fits and Train-only nested cross-fitting. Cached pretrained weights are required for offline use and are not embedded in joblib artifacts.

## Optional Laya experiments

`laya_zeroshot` uses compact reviewed questions with 256-token article windows and 64-token overlap. `laya_full_definition` sends the complete English definitions with 128-token windows, 32-token overlap and an explicit 384-token question budget. `laya_compact_control` uses those same budgets with compact questions. All three now use only the 11 active labels.

`models/risk_questions.json` contains the retained labels' original English definitions, inclusion/exclusion rules, compact prompts, source cells and workbook SHA-256. The original reviewed taxonomy workbook is not modified or required at runtime. These models do not learn from the intersection targets.

Use a dedicated environment with `requirements-laya.txt`. The configuration remains `laya==0.4.11`, `convaiinnovations/laya` commit `7b928d828b7b0e022f929d9bd2e44165aa270148`, CPU FP32, independent neutral A/B binary choices, max-over-windows aggregation and fixed 0.5 cutoff. Runtime checks reject question truncation and dropped article-window content. INT8 is not included.

```powershell
.\.venv-laya\Scripts\python.exe run.py --model laya_full_definition --preview 1
```

Preview uses Train only and creates no benchmark run or comparison entry. Historical 14-label CPU previews overpredicted tags; no new 11-label Laya GPU/CPU quality result is claimed by this scope update. Check a preview before spending time on a full semantic benchmark.

The separately delivered `qwen_teacher_gpu` and student-addon packages are independent historical experiments. This archive updates the terminal research project only; do not copy their old 14-label teacher caches into the new dataset or mix their reports into this comparison.

## Checks

```powershell
python -m unittest discover -s tests -v
```

Checks cover evaluation, OOF isolation, frozen window coverage, nested stacking, Laya contracts, 11-label data integrity, and rejection of incompatible historical outputs. The full offline suite needs `tqdm` from either optional requirements file for progress-bar code. Downloaded model-weight inference is not required for these tests.
