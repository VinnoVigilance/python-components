"""Compare a TF-IDF/LinearSVC candidate with the frozen logistic baseline.

Train-only feature fitting, independent binary classifiers, unknown (-1)
targets excluded per label. Validation only; never reads test contents.
SVM decision scores are signed margins, NOT probabilities or risk severity.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import average_precision_score
from sklearn.svm import LinearSVC

try:
    from audit_dataset import LABEL_NAMES, file_hash
    from prepare_dataset import json_lines, write_atomic
    from train_tfidf_lr import CONFIG as LR_CONFIG
    from train_tfidf_lr import load_partition, counts_and_metrics, save_model, format_metric
except ModuleNotFoundError as error:
    if error.name not in {"audit_dataset", "prepare_dataset", "train_tfidf_lr"}:
        raise
    raise SystemExit("ERROR: put this file alongside audit_dataset.py, prepare_dataset.py and train_tfidf_lr.py.") from error


MODEL_VERSION = "tfidf-linear-svm-silver-v1"
THRESHOLD = 0.0
CONFIG = {
    "vectorizer": deepcopy(LR_CONFIG["vectorizer"]),
    "classifier": {
        "C": 1.0, "penalty": "l2", "loss": "squared_hinge",
        "dual": "auto", "tol": 0.0001, "max_iter": 10000,
        "class_weight": None, "random_state": 42,
    },
    "initial_threshold": THRESHOLD,
    "prediction_rule": "decision_score > threshold",
    "score_type": "uncalibrated_signed_margin",
    "unknown_target": -1,
}


def main() -> int:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits-dir", type=Path, default=base / "data" / "processed" / "splits")
    parser.add_argument("--baseline-dir", type=Path, default=base / "outputs" / "tfidf_lr_baseline_v1")
    parser.add_argument("--output-dir", type=Path, default=base / "outputs" / "tfidf_svm_baseline_v1")
    args = parser.parse_args()
    started = time.perf_counter()
    try:
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ValueError("Output directory contains files. Preserve this run; use --output-dir with a new directory.")
        manifest_path = args.splits_dir / "split_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["labels"] != list(LABEL_NAMES) or manifest["target_encoding"] != {
            "positive": 1, "provisional_negative": 0, "unknown": -1
        }:
            raise ValueError("Unexpected frozen label order or encoding.")
        train, train_y = load_partition(args.splits_dir, "train", manifest)
        validation, validation_y = load_partition(args.splits_dir, "validation", manifest)
        for key in ("news_id", "body_hash"):
            if {r[key] for r in train} & {r[key] for r in validation}:
                raise ValueError(f"Train/validation overlap: {key}.")
        if {v for r in train for v in r["original_news_ids"]} & {v for r in validation for v in r["original_news_ids"]}:
            raise ValueError("Train/validation original ID overlap.")
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
        if baseline["model_version"] != "tfidf-lr-silver-baseline-v1" or baseline["labels"] != list(LABEL_NAMES):
            raise ValueError("Expected original logistic baseline report.")
        if baseline["lineage"] != lineage or baseline["config"]["vectorizer"] != CONFIG["vectorizer"]:
            raise ValueError("Baseline data lineage or TF-IDF settings differ. Cannot make a controlled comparison.")
        if baseline["config"]["classifier"]["class_weight"] is not None or baseline["config"]["initial_threshold"] != 0.5:
            raise ValueError("Expected original unweighted logistic baseline with threshold 0.5.")

        print(f"Train news: {len(train)}", flush=True)
        print(f"Validation news: {len(validation)}", flush=True)
        print("Fitting unchanged TF-IDF settings on TRAIN only...", flush=True)
        vectorizer_options = {**CONFIG["vectorizer"], "ngram_range": tuple(CONFIG["vectorizer"]["ngram_range"])}
        vectorizer = TfidfVectorizer(**vectorizer_options)
        tick = time.perf_counter()
        train_x = vectorizer.fit_transform([r["text"] for r in train])
        validation_x = vectorizer.transform([r["text"] for r in validation])
        feature_seconds = time.perf_counter() - tick
        classifiers, per_label = {}, {}
        scores = np.full(validation_y.shape, np.nan, dtype=np.float64)
        fit_seconds = inference_seconds = 0.0
        for index, label in enumerate(LABEL_NAMES):
            known_train = train_y[:, index] != -1
            target = train_y[known_train, index]
            positive, negative = int(np.sum(target == 1)), int(np.sum(target == 0))
            support = {
                "train_positive": positive, "train_provisional_negative": negative,
                "train_unknown_excluded": int(np.sum(~known_train)),
                "validation_positive": int(np.sum(validation_y[:, index] == 1)),
                "validation_provisional_negative": int(np.sum(validation_y[:, index] == 0)),
                "validation_unknown_excluded": int(np.sum(validation_y[:, index] == -1)),
                "low_training_positive_support": positive < 20, "threshold": THRESHOLD,
            }
            if any(baseline["per_label"][label][k] != support[k] for k in support if k.startswith(("train_", "validation_"))):
                raise ValueError(f"Baseline support differs for {label}.")
            if not positive or not negative:
                classifiers[label] = None
                per_label[label] = {**support, "status": "skipped_missing_training_class", "metrics": None}
                continue
            classifier = LinearSVC(**CONFIG["classifier"])
            tick = time.perf_counter()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                classifier.fit(train_x[known_train], target)
            if any(issubclass(w.category, ConvergenceWarning) for w in caught):
                raise ValueError(f"SVM did not converge for {label}; inspect before using results.")
            fit_seconds += time.perf_counter() - tick
            if list(classifier.classes_) != [0, 1]:
                raise ValueError(f"Unexpected SVM score direction for {label}.")
            tick = time.perf_counter()
            scores[:, index] = classifier.decision_function(validation_x)
            inference_seconds += time.perf_counter() - tick
            if not np.all(np.isfinite(scores[:, index])):
                raise ValueError(f"Non-finite SVM scores for {label}.")
            classifiers[label] = classifier
            mask = validation_y[:, index] != -1
            truth, label_scores = validation_y[mask, index], scores[mask, index]
            metrics = counts_and_metrics(truth, label_scores > THRESHOLD)
            metrics["average_precision"] = float(average_precision_score(truth, label_scores)) if (
                np.any(truth == 1) and np.any(truth == 0)
            ) else None
            per_label[label] = {**support, "status": "trained_experimental", "metrics": metrics}
            print(f"[{index + 1}/23] {label}: positive={positive}, negative={negative}, unknown excluded={support['train_unknown_excluded']}", flush=True)

        available, known = np.isfinite(scores), validation_y != -1
        evaluated = available & known
        if not np.any(evaluated):
            raise ValueError("No evaluable validation pairs.")
        predicted = scores > THRESHOLD
        micro = counts_and_metrics(validation_y[evaluated], predicted[evaluated])
        macro_values = [r["metrics"]["f1"] for r in per_label.values()
                        if r["metrics"] is not None and r["validation_positive"] > 0 and r["metrics"]["f1"] is not None]
        fully_known = np.all(evaluated, axis=1)
        summary = {
            "trained_classifiers": sum(c is not None for c in classifiers.values()),
            "skipped_classifiers": sum(c is None for c in classifiers.values()),
            "evaluated_known_pairs": int(np.sum(evaluated)),
            "validation_unknown_pairs_excluded": int(np.sum(~known)),
            "known_pairs_without_classifier": int(np.sum(known & ~available)),
            "validation_micro": micro,
            "validation_macro_f1_positive_supported_labels": float(np.mean(macro_values)) if macro_values else None,
            "macro_f1_label_count": len(macro_values),
            "fully_known_and_scored_articles": int(np.sum(fully_known)),
            "exact_match_fully_known_and_scored_articles": float(np.mean(np.all(predicted[fully_known] == validation_y[fully_known], axis=1))) if np.any(fully_known) else None,
            "tfidf_features": len(vectorizer.vocabulary_),
            "feature_build_seconds": feature_seconds, "classifier_fit_seconds": fit_seconds,
            "validation_predict_seconds_excluding_vectorization": inference_seconds,
        }
        for k in ("evaluated_known_pairs", "known_pairs_without_classifier", "macro_f1_label_count"):
            if baseline["summary"][k] != summary[k]:
                raise ValueError(f"Baseline evaluation coverage differs: {k}.")
        comparable_ap = [label for label in LABEL_NAMES if per_label[label]["metrics"] is not None
                         and baseline["per_label"][label]["metrics"] is not None
                         and per_label[label]["metrics"]["average_precision"] is not None
                         and baseline["per_label"][label]["metrics"]["average_precision"] is not None]
        comparison = {
            "baseline_report_sha256": file_hash(baseline_path),
            "baseline_model_version": baseline["model_version"],
            "baseline_micro": baseline["summary"]["validation_micro"], "svm_micro": micro,
            "baseline_macro_f1": baseline["summary"]["validation_macro_f1_positive_supported_labels"],
            "svm_macro_f1": summary["validation_macro_f1_positive_supported_labels"],
            "average_precision_labels": comparable_ap,
            "baseline_macro_average_precision": float(np.mean([baseline["per_label"][l]["metrics"]["average_precision"] for l in comparable_ap])) if comparable_ap else None,
            "svm_macro_average_precision": float(np.mean([per_label[l]["metrics"]["average_precision"] for l in comparable_ap])) if comparable_ap else None,
            "interpretation": "Default decision rules compared; no threshold tuning. Average precision compares ranking on identical known pairs. No winner selected.",
        }
        environment = {"python": platform.python_version(), **{n: importlib.metadata.version(n) for n in ("numpy", "scipy", "scikit-learn", "joblib")}}
        bundle = {
            "model_version": MODEL_VERSION, "vectorizer": vectorizer, "classifiers": classifiers,
            "labels": list(LABEL_NAMES), "thresholds": {l: THRESHOLD for l in LABEL_NAMES},
            "score_type": CONFIG["score_type"], "prediction_rule": CONFIG["prediction_rule"],
            "config": CONFIG, "lineage": lineage, "environment": environment,
            "label_training_support": {l: {k: v for k, v in r.items() if k.startswith("train_") or k == "low_training_positive_support"} for l, r in per_label.items()},
            "deployment_status": "experimental_not_promoted",
        }
        predictions = [{
            "news_id": r["news_id"], "excel_row": r["excel_row"], "title": r["title"], "text": r["text"],
            "reference_shared_labels": r["shared_labels"], "reference_label_values": r["label_values"],
            "predicted_labels": [l for j, l in enumerate(LABEL_NAMES) if available[i, j] and predicted[i, j]],
            "score_type": CONFIG["score_type"],
            "raw_decision_score_by_label": {l: float(scores[i, j]) if available[i, j] else None for j, l in enumerate(LABEL_NAMES)},
        } for i, r in enumerate(validation)]
        args.output_dir.mkdir(parents=True, exist_ok=True)
        model_hash = save_model(args.output_dir / "model.joblib", bundle)
        prediction_hash = write_atomic(args.output_dir / "validation_predictions.jsonl", json_lines(predictions))
        report = {
            "schema_version": 1, "model_version": MODEL_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "labels": list(LABEL_NAMES), "config": CONFIG, "environment": environment, "lineage": lineage,
            "train_news": len(train), "validation_news": len(validation), "summary": summary,
            "per_label": per_label, "comparison_to_lr": comparison,
            "artifacts": {"model_sha256": model_hash, "validation_predictions_sha256": prediction_hash},
            "elapsed_seconds": time.perf_counter() - started,
            "limitations": [
                "Metrics measure agreement with silver labels, not human-reviewed accuracy.",
                "Unknown targets are excluded per label; metrics describe the known subset.",
                "SVM margins are not probabilities, risk severity, or comparable across different labels.",
                "Default SVM threshold 0 and LR threshold 0.5 are not matched precision operating points.",
                "Rare-label performance cannot be established with the available small support.",
                "Macro-F1 excludes labels without positive validation support.",
                "Test contents were not read; repeated validation comparisons can overfit model selection.",
                "Experimental model; no production promotion or model winner selected.",
            ],
        }
        write_atomic(args.output_dir / "training_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("\nSVM TRAINING COMPLETED")
        print(f"Trained classifiers: {summary['trained_classifiers']}/23")
        for name in ("precision", "recall", "f1"):
            print(f"Validation micro {name} (LR -> SVM): {format_metric(comparison['baseline_micro'][name])} -> {format_metric(micro[name])}")
        print(f"Validation macro F1 (LR -> SVM): {format_metric(comparison['baseline_macro_f1'])} -> {format_metric(comparison['svm_macro_f1'])}")
        print(f"Macro F1 label count: {summary['macro_f1_label_count']}/23")
        print(f"Macro average precision (LR -> SVM): {format_metric(comparison['baseline_macro_average_precision'])} -> {format_metric(comparison['svm_macro_average_precision'])}")
        print(f"Average precision label count: {len(comparable_ap)}/23")
        print(f"Validation unknown pairs excluded: {summary['validation_unknown_pairs_excluded']}")
        print(f"Elapsed seconds: {report['elapsed_seconds']:.1f}")
        print("Score type: signed margin, NOT probability")
        print("Test contents used: NO")
        print(f"Files saved to: {args.output_dir}")
        print("Consensus agreement only. Default thresholds; no winner selected.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
