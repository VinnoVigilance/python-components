"""One controlled VV experiment: word + character TF-IDF with LinearSVC.

Uses the frozen train/validation split. Never reads test contents. Unknown
targets (-1) are excluded separately for each label. Compares both models
at threshold zero AND under the same validation threshold-selection policy.
"""
from __future__ import annotations

import argparse
import csv
from copy import deepcopy
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sys
import time
import uuid
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import average_precision_score
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import Normalizer
from sklearn.svm import LinearSVC

try:
    from audit_dataset import LABEL_NAMES, file_hash
    from prepare_dataset import json_lines, write_atomic
    from train_tfidf_lr import load_partition, counts_and_metrics, save_model, format_metric
    from train_tfidf_svm import CONFIG as ORIGINAL_CONFIG
    from analyze_svm_thresholds import curve, check_metrics, MANIFEST_SHA, VALIDATION_SHA
except ModuleNotFoundError as error:
    if error.name not in {"audit_dataset", "prepare_dataset", "train_tfidf_lr", "train_tfidf_svm", "analyze_svm_thresholds"}:
        raise
    raise SystemExit(f"Missing companion {error.name}.py; keep this script beside the earlier VV experiment scripts.") from error

MODEL_VERSION = "tfidf-word-char-linear-svm-silver-v1"
CONFIG = {
    "word_vectorizer": deepcopy(ORIGINAL_CONFIG["vectorizer"]),
    "char_vectorizer": {
        "analyzer": "char_wb", "ngram_range": [3, 5],
        "min_df": 2, "max_df": 0.95, "max_features": 100000,
        "sublinear_tf": True, "lowercase": True, "stop_words": None,
    },
    "feature_weights": {"word": 1.0, "char": 1.0},
    "combined_normalization": "l2",
    "classifier": deepcopy(ORIGINAL_CONFIG["classifier"]),
    "initial_threshold": 0.0, "prediction_rule": "decision_score > threshold",
    "score_type": "uncalibrated_signed_margin", "unknown_target": -1,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def evaluate_scope(targets, scores, thresholds, indices):
    truth = targets[:, indices]
    subset_scores = scores[:, indices]
    known = truth != -1
    predicted = subset_scores > thresholds[indices]
    micro = counts_and_metrics(truth[known], predicted[known])
    f1s, aps = [], []
    for j in range(len(indices)):
        mask = known[:, j]
        y = truth[mask, j]
        if np.any(y == 1):
            f1s.append(counts_and_metrics(y, predicted[mask, j])["f1"])
            if np.any(y == 0):
                aps.append(float(average_precision_score(y, subset_scores[mask, j])))
    return {
        "micro": micro, "macro_f1": float(np.mean(f1s)) if f1s else None,
        "macro_f1_label_count": len(f1s),
        "macro_average_precision": float(np.mean(aps)) if aps else None,
        "average_precision_label_count": len(aps),
        "evaluated_known_pairs": int(known.sum()),
        "unknown_pairs_excluded": int((~known).sum()),
        "predicted_positive_unknown_pairs": int((predicted & ~known).sum()),
    }


def select_thresholds(targets, scores, primary_indices):
    thresholds = np.zeros(23, dtype=np.float64)
    statuses = {label: "rare_label_unchanged_not_validated" for label in LABEL_NAMES}
    for j in primary_indices:
        known = targets[:, j] != -1
        pairs = list(zip(scores[known, j].tolist(), targets[known, j].tolist()))
        candidates = [p for p in curve(pairs)
                      if p["metrics"]["predicted_positive_known_pairs"] >= 10
                      and (p["metrics"]["precision"] or 0) + 1e-12 >= 0.95]
        if candidates:
            selected = max(candidates, key=lambda p: (p["metrics"]["recall"] or 0,
                                                       p["metrics"]["precision"] or 0, p["threshold"]))
            thresholds[j] = selected["threshold"]
            statuses[LABEL_NAMES[j]] = "precision_threshold_proposed"
        else:
            statuses[LABEL_NAMES[j]] = "no_feasible_precision_threshold_default_retained"
    return thresholds, statuses


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits-dir", type=Path, default=here / "data/processed/splits")
    parser.add_argument("--baseline-dir", type=Path, default=here / "outputs/tfidf_svm_baseline_v1")
    parser.add_argument("--output-root", type=Path, default=here / "outputs/word_char_svm_baseline_v1")
    args = parser.parse_args()
    started = time.perf_counter()
    manifest_path = args.splits_dir / "split_manifest.json"
    require(file_hash(manifest_path) == MANIFEST_SHA, "Frozen split manifest changed.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    labels = list(LABEL_NAMES)
    require(manifest["labels"] == labels and len(labels) == 23, "Label order mismatch.")
    require(manifest["target_encoding"] == {"positive": 1, "provisional_negative": 0, "unknown": -1}, "Target encoding mismatch.")
    train, train_y = load_partition(args.splits_dir, "train", manifest)
    validation, validation_y = load_partition(args.splits_dir, "validation", manifest)
    require(len(train) == 3395 and len(validation) == 728, "Frozen split counts changed.")
    require(file_hash(args.splits_dir / "validation.jsonl") == VALIDATION_SHA, "Frozen validation changed.")
    for field in ("news_id", "body_hash"):
        require(not ({r[field] for r in train} & {r[field] for r in validation}), f"Train/validation overlap: {field}.")
    require(not ({v for r in train for v in r["original_news_ids"]} &
                 {v for r in validation for v in r["original_news_ids"]}), "Original ID overlap.")
    lineage = {
        "input_dataset_sha256": manifest["input_dataset_sha256"],
        "split_manifest_sha256": file_hash(manifest_path),
        "train_sha256": manifest["splits"]["train"]["sha256"],
        "validation_sha256": manifest["splits"]["validation"]["sha256"],
        "preprocessing_version": manifest["input_preprocessing_version"],
        "label_source": manifest["label_source"],
    }
    baseline_path = args.baseline_dir / "training_report.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    require(baseline["model_version"] == "tfidf-linear-svm-silver-v1" and baseline["labels"] == labels, "Expected original word SVM report.")
    require(baseline["lineage"] == lineage, "Baseline data lineage mismatch.")
    require(baseline["config"] == ORIGINAL_CONFIG, "Expected unchanged original SVM settings.")
    predictions_path = args.baseline_dir / "validation_predictions.jsonl"
    require(file_hash(predictions_path) == baseline["artifacts"]["validation_predictions_sha256"], "Baseline prediction hash mismatch.")
    old_predictions = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    require([r["news_id"] for r in old_predictions] == [r["news_id"] for r in validation], "Cached baseline record order mismatch.")
    old_scores = np.empty(validation_y.shape, dtype=np.float64)
    for i, (row, pred) in enumerate(zip(validation, old_predictions)):
        require(row["text"] == pred["text"] and row["label_values"] == pred["reference_label_values"], "Cached baseline input/reference mismatch.")
        require(pred["score_type"] == CONFIG["score_type"] and set(pred["raw_decision_score_by_label"]) == set(labels), "Cached baseline score schema mismatch.")
        old_scores[i] = [pred["raw_decision_score_by_label"][label] for label in labels]
        require(pred["predicted_labels"] == [label for j, label in enumerate(labels) if old_scores[i, j] > 0], "Baseline default prediction mismatch.")
    require(np.isfinite(old_scores).all(), "Invalid baseline scores.")
    primary_indices = [j for j in range(23) if int((train_y[:, j] == 1).sum()) >= 20
                       and int((validation_y[:, j] == 1).sum()) >= 10]
    require(len(primary_indices) == 14, "PRIMARY_14 support policy changed.")
    zero = np.zeros(23)
    old_default = evaluate_scope(validation_y, old_scores, zero, list(range(23)))
    check_metrics(old_default["micro"], baseline["summary"]["validation_micro"], "original baseline")

    print(f"Train news: {len(train)}; validation news: {len(validation)}", flush=True)
    print("Frozen split and cached baseline: OK", flush=True)
    print("Fitting word + char_wb TF-IDF on TRAIN only...", flush=True)
    options = lambda c: {**c, "ngram_range": tuple(c["ngram_range"])}
    vectorizer = Pipeline([
        ("features", FeatureUnion([
            ("word", TfidfVectorizer(**options(CONFIG["word_vectorizer"]))),
            ("char", TfidfVectorizer(**options(CONFIG["char_vectorizer"]))),
        ], transformer_weights=CONFIG["feature_weights"])),
        ("normalizer", Normalizer(norm="l2")),
    ])
    tick = time.perf_counter()
    train_x = vectorizer.fit_transform([r["text"] for r in train]).tocsr()
    feature_fit_seconds = time.perf_counter() - tick
    tick = time.perf_counter()
    validation_x = vectorizer.transform([r["text"] for r in validation]).tocsr()
    validation_vectorization_seconds = time.perf_counter() - tick
    feature_sizes = {name: len(v.vocabulary_) for name, v in vectorizer.named_steps["features"].transformer_list}
    print(f"Features: word={feature_sizes['word']}, character={feature_sizes['char']}, combined={train_x.shape[1]}", flush=True)

    scores = np.empty(validation_y.shape, dtype=np.float64)
    classifiers, per_label = {}, {}
    fit_seconds = predict_seconds = 0.0
    for j, label in enumerate(labels):
        known = train_y[:, j] != -1
        y = train_y[known, j]
        positive, negative = int((y == 1).sum()), int((y == 0).sum())
        support = {
            "train_positive": positive, "train_provisional_negative": negative,
            "train_unknown_excluded": int((~known).sum()),
            "validation_positive": int((validation_y[:, j] == 1).sum()),
            "validation_provisional_negative": int((validation_y[:, j] == 0).sum()),
            "validation_unknown_excluded": int((validation_y[:, j] == -1).sum()),
        }
        require(all(baseline["per_label"][label][k] == v for k, v in support.items()), f"Baseline label support mismatch: {label}.")
        mask = validation_y[:, j] != -1
        check_metrics(counts_and_metrics(validation_y[mask, j], old_scores[mask, j] > 0), baseline["per_label"][label]["metrics"], label)
        require(positive > 0 and negative > 0, f"Missing known training class: {label}.")
        classifier = LinearSVC(**CONFIG["classifier"])
        tick = time.perf_counter()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            classifier.fit(train_x[known], y)
        require(not any(issubclass(w.category, ConvergenceWarning) for w in caught), f"Classifier did not converge: {label}.")
        require(list(classifier.classes_) == [0, 1], "Unexpected score direction.")
        fit_seconds += time.perf_counter() - tick
        tick = time.perf_counter()
        scores[:, j] = classifier.decision_function(validation_x)
        predict_seconds += time.perf_counter() - tick
        require(np.isfinite(scores[:, j]).all(), "Non-finite candidate scores.")
        classifiers[label] = classifier
        metric = counts_and_metrics(validation_y[mask, j], scores[mask, j] > 0)
        metric["average_precision"] = float(average_precision_score(validation_y[mask, j], scores[mask, j])) if np.any(validation_y[mask, j] == 1) and np.any(validation_y[mask, j] == 0) else None
        per_label[label] = {**support, "low_training_positive_support": positive < 20,
                            "threshold": 0.0, "status": "trained_experimental", "metrics": metric}
        print(f"[{j + 1}/23] {label}: positive={positive}, negative={negative}, unknown excluded={support['train_unknown_excluded']}", flush=True)

    old_tuned, old_status = select_thresholds(validation_y, old_scores, primary_indices)
    new_tuned, new_status = select_thresholds(validation_y, scores, primary_indices)
    comparisons = {}
    for scope, indices in (("PRIMARY_14", primary_indices), ("ORIGINAL_23", list(range(23)))):
        comparisons[scope] = {
            "default": {"word_svm": evaluate_scope(validation_y, old_scores, zero, indices),
                        "word_char_svm": evaluate_scope(validation_y, scores, zero, indices)},
            "same_threshold_policy": {"word_svm": evaluate_scope(validation_y, old_scores, old_tuned, indices),
                                      "word_char_svm": evaluate_scope(validation_y, scores, new_tuned, indices)},
        }
    default_full = comparisons["ORIGINAL_23"]["default"]["word_char_svm"]
    environment = {"python": platform.python_version(), **{p: importlib.metadata.version(p) for p in ("numpy", "scipy", "scikit-learn", "joblib")}}
    bundle = {
        "model_version": MODEL_VERSION, "vectorizer": vectorizer, "classifiers": classifiers,
        "labels": labels, "thresholds": {label: 0.0 for label in labels},
        "score_type": CONFIG["score_type"], "prediction_rule": CONFIG["prediction_rule"],
        "config": CONFIG, "lineage": lineage, "environment": environment,
        "label_training_support": {l: {k: v for k, v in item.items() if k.startswith("train_") or k == "low_training_positive_support"} for l, item in per_label.items()},
        "deployment_status": "experimental_not_promoted",
    }
    policy = {
        "precision_floor_on_known_silver_pairs": 0.95, "min_predicted_positive_known_pairs": 10,
        "eligible_labels": "train_positive >= 20 and validation_positive >= 10",
        "objective": "maximize per-label recall; break ties by precision then higher threshold",
        "ineligible_or_infeasible": "default threshold retained; not validated",
        "business_policy_approved": False,
    }
    proposal = {
        "schema_version": 1, "base_model_version": MODEL_VERSION,
        "status": "validation_proposal_not_approved_for_production", "labels": labels,
        "primary_labels": [labels[j] for j in primary_indices], "policy": policy,
        "score_type": CONFIG["score_type"], "prediction_rule": CONFIG["prediction_rule"],
        "thresholds": {label: float(new_tuned[j]) for j, label in enumerate(labels)},
        "label_status": new_status, "lineage": lineage,
    }
    predictions = [{
        "news_id": row["news_id"], "excel_row": row["excel_row"], "title": row["title"], "text": row["text"],
        "reference_shared_labels": row["shared_labels"], "reference_label_values": row["label_values"],
        "predicted_labels": [label for j, label in enumerate(labels) if scores[i, j] > 0],
        "proposed_predicted_labels": [label for j, label in enumerate(labels) if scores[i, j] > new_tuned[j]],
        "score_type": CONFIG["score_type"],
        "raw_decision_score_by_label": {label: float(scores[i, j]) for j, label in enumerate(labels)},
    } for i, row in enumerate(validation)]
    created = datetime.now(timezone.utc)
    output = args.output_root / (created.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    model_sha = save_model(output / "model.joblib", bundle)
    predictions_sha = write_atomic(output / "validation_predictions.jsonl", json_lines(predictions))
    summary = {
        "trained_classifiers": 23, "skipped_classifiers": 0,
        "evaluated_known_pairs": default_full["evaluated_known_pairs"],
        "validation_unknown_pairs_excluded": default_full["unknown_pairs_excluded"],
        "known_pairs_without_classifier": 0, "validation_micro": default_full["micro"],
        "validation_macro_f1_positive_supported_labels": default_full["macro_f1"],
        "macro_f1_label_count": default_full["macro_f1_label_count"],
        "tfidf_features": train_x.shape[1], "feature_counts": feature_sizes,
        "feature_fit_seconds": feature_fit_seconds,
        "validation_vectorization_seconds": validation_vectorization_seconds,
        "classifier_fit_seconds": fit_seconds,
        "validation_predict_seconds_excluding_vectorization": predict_seconds,
    }
    report = {
        "schema_version": 1, "model_version": MODEL_VERSION, "created_at_utc": created.isoformat(),
        "labels": labels, "config": CONFIG, "environment": environment, "lineage": lineage,
        "train_news": len(train), "validation_news": len(validation), "summary": summary,
        "per_label": per_label, "comparison_to_word_svm": comparisons,
        "threshold_policy": policy,
        "threshold_proposals": {
            "word_svm": {"thresholds": {label: float(old_tuned[j]) for j, label in enumerate(labels)}, "label_status": old_status},
            "word_char_svm": proposal,
        },
        "artifacts": {"model_sha256": model_sha, "validation_predictions_sha256": predictions_sha,
                      "baseline_report_sha256": file_hash(baseline_path),
                      "baseline_predictions_sha256": file_hash(predictions_path),
                      "script_sha256": file_hash(Path(__file__))},
        "elapsed_seconds": time.perf_counter() - started,
        "limitations": [
            "Metrics measure consensus silver agreement on known pairs, not human-reviewed accuracy.",
            "Threshold selection and evaluation use the same validation records; proposed metrics are selection results.",
            "A precision floor and minimum support are practical policies, not statistical guarantees.",
            "Rare-label default thresholds are retained for comparison only, not release approval.",
            "Character features do not add sentence-level reasoning, probability calibration or evidence extraction.",
            "Compute timings are single-pass measurements, not a production latency/throughput benchmark.",
            "Test contents were not read. Repeated validation model selection can overfit.",
            "Saved model uses default thresholds. Proposals are separate; no promotion or final winner selected.",
        ],
    }
    write_atomic(output / "training_report.json", json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    write_atomic(output / "proposed_thresholds.json", json.dumps(proposal, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    with (output / "per_label_comparison.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "primary_label", "train_positive", "validation_positive",
                         "word_status", "word_char_status", "word_threshold", "word_char_threshold",
                         "word_precision", "word_char_precision", "word_recall", "word_char_recall",
                         "word_f1", "word_char_f1", "word_average_precision", "word_char_average_precision"])
        for j, label in enumerate(labels):
            a = evaluate_scope(validation_y, old_scores, old_tuned, [j])
            b = evaluate_scope(validation_y, scores, new_tuned, [j])
            writer.writerow([label, j in primary_indices, per_label[label]["train_positive"], per_label[label]["validation_positive"],
                             old_status[label], new_status[label], old_tuned[j], new_tuned[j],
                             a["micro"]["precision"], b["micro"]["precision"], a["micro"]["recall"], b["micro"]["recall"],
                             a["micro"]["f1"], b["micro"]["f1"], a["macro_average_precision"], b["macro_average_precision"]])
    shutil.copyfile(Path(__file__), output / Path(__file__).name)
    print("\nWORD + CHARACTER SVM TRAINING COMPLETED")
    print("Trained classifiers: 23/23; primary labels: 14; rare labels: 9")
    for scope, comparison in comparisons.items():
        print("\n" + scope + " (word SVM -> word+character SVM)")
        for mode, results in comparison.items():
            a, b = results["word_svm"], results["word_char_svm"]
            print("Comparison:", mode)
            for metric in ("precision", "recall", "f1"):
                print(f"  Micro {metric}: {format_metric(a['micro'][metric])} -> {format_metric(b['micro'][metric])}")
            for key, label in (("macro_f1", "Macro F1"), ("macro_average_precision", "Macro AP")):
                print(f"  {label}: {format_metric(a[key])} -> {format_metric(b[key])}")
            print(f"  Positive predictions on unknown pairs: {a['predicted_positive_unknown_pairs']} -> {b['predicted_positive_unknown_pairs']}")
    print("\nFeasible precision-policy labels:", sum(old_status[labels[j]] == "precision_threshold_proposed" for j in primary_indices), "->", sum(new_status[labels[j]] == "precision_threshold_proposed" for j in primary_indices))
    print(f"Elapsed seconds: {report['elapsed_seconds']:.1f}")
    print("Model training: YES; feature fitting: TRAIN ONLY; unknown targets excluded: YES")
    print("Test contents used / original model modified / production promotion: NO")
    print("Saved model thresholds: DEFAULT ZERO; threshold proposals stored separately.")
    print("CPU production benchmark: NOT PERFORMED")
    print("Files saved to:", output.resolve())
    print("Consensus agreement and validation selection only; no final winner selected.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Word/character SVM failed: {error}", file=sys.stderr)
        sys.exit(1)
