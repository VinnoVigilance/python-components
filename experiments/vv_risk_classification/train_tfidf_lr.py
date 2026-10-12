"""Train the first experimental TF-IDF/logistic baseline on VV silver labels.

Fits text features on train only, excludes -1 per label, evaluates validation
at a fixed initial threshold of 0.5. Never reads test.jsonl. This measures
agreement with consensus targets, not human-validated risk-category accuracy.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import warnings

import joblib
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

try:
    from audit_dataset import LABEL_NAMES, file_hash
    from prepare_dataset import json_lines, write_atomic
except ModuleNotFoundError as error:
    if error.name not in {"audit_dataset", "prepare_dataset"}:
        raise
    raise SystemExit("ERROR: put this script next to audit_dataset.py and prepare_dataset.py.") from error


MODEL_VERSION = "tfidf-lr-silver-baseline-v1"
THRESHOLD = 0.5
MIN_SUPPORT_WARNING = 20
CONFIG = {
    "vectorizer": {
        "ngram_range": [1, 2], "min_df": 2, "max_df": 0.95,
        "max_features": 100000, "sublinear_tf": True,
        "lowercase": True, "stop_words": None,
    },
    "classifier": {
        "C": 1.0, "solver": "liblinear", "max_iter": 2000,
        "class_weight": None, "random_state": 42,
    },
    "initial_threshold": THRESHOLD,
    "unknown_target": -1,
}


def load_partition(directory: Path, name: str, manifest: dict) -> tuple[list[dict], np.ndarray]:
    path = directory / f"{name}.jsonl"
    if not path.is_file() or file_hash(path) != manifest["splits"][name]["sha256"]:
        raise ValueError(f"Frozen {name} file is missing or changed.")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if [row["news_id"] for row in rows] != manifest["splits"][name]["news_ids"]:
        raise ValueError(f"Frozen {name} IDs/order do not match the manifest.")
    if len(rows) != manifest["splits"][name]["rows"]:
        raise ValueError(f"Frozen {name} row count does not match the manifest.")
    for row in rows:
        values = row.get("label_values", [])
        if len(values) != len(LABEL_NAMES) or any(type(v) is not int or v not in (-1, 0, 1) for v in values):
            raise ValueError(f"Invalid targets for {row.get('news_id')}.")
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            raise ValueError(f"Missing classifier text for {row.get('news_id')}.")
    return rows, np.asarray([row["label_values"] for row in rows], dtype=np.int8)


def counts_and_metrics(truth: np.ndarray, predicted: np.ndarray) -> dict:
    tp = int(np.sum((truth == 1) & (predicted == 1)))
    fp = int(np.sum((truth == 0) & (predicted == 1)))
    fn = int(np.sum((truth == 1) & (predicted == 0)))
    tn = int(np.sum((truth == 0) & (predicted == 0)))
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def save_model(path: Path, bundle: dict) -> str:
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, suffix=".joblib")
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        joblib.dump(bundle, temporary_path, compress=3)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return file_hash(path)


def format_metric(value: float | None) -> str:
    return "not estimable" if value is None else f"{value:.4f}"


def main() -> int:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits-dir", type=Path, default=base / "data" / "processed" / "splits")
    parser.add_argument("--output-dir", type=Path, default=base / "outputs" / "tfidf_lr_baseline_v1")
    args = parser.parse_args()
    start = time.perf_counter()
    try:
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ValueError("Output directory already contains files. Keep this run; use --output-dir with a new directory for a new run.")
        manifest_path = args.splits_dir / "split_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["labels"] != list(LABEL_NAMES) or manifest["target_encoding"] != {
            "positive": 1, "provisional_negative": 0, "unknown": -1
        }:
            raise ValueError("Unexpected frozen label names/order or target encoding.")
        train, train_y = load_partition(args.splits_dir, "train", manifest)
        validation, validation_y = load_partition(args.splits_dir, "validation", manifest)
        if {row["news_id"] for row in train} & {row["news_id"] for row in validation}:
            raise ValueError("Train/validation ID overlap detected.")
        if {row["body_hash"] for row in train} & {row["body_hash"] for row in validation}:
            raise ValueError("Train/validation exact body overlap detected.")
        train_original_ids = {value for row in train for value in row["original_news_ids"]}
        validation_original_ids = {value for row in validation for value in row["original_news_ids"]}
        if train_original_ids & validation_original_ids:
            raise ValueError("Train/validation original ID overlap detected.")

        print(f"Train news: {len(train)}", flush=True)
        print(f"Validation news: {len(validation)}", flush=True)
        print("Fitting TF-IDF on TRAIN text only...", flush=True)
        vectorizer_config = {**CONFIG["vectorizer"], "ngram_range": tuple(CONFIG["vectorizer"]["ngram_range"])}
        vectorizer = TfidfVectorizer(**vectorizer_config)
        features_started = time.perf_counter()
        train_x = vectorizer.fit_transform([row["text"] for row in train])
        validation_x = vectorizer.transform([row["text"] for row in validation])
        feature_seconds = time.perf_counter() - features_started
        print(f"TF-IDF features: {len(vectorizer.vocabulary_)}", flush=True)

        classifiers = {}
        label_reports = {}
        scores = np.full(validation_y.shape, np.nan, dtype=np.float64)
        fit_seconds = inference_seconds = 0.0
        for index, label in enumerate(LABEL_NAMES):
            known_train = train_y[:, index] != -1
            target = train_y[known_train, index]
            positive = int(np.sum(target == 1))
            negative = int(np.sum(target == 0))
            metadata = {
                "train_positive": positive,
                "train_provisional_negative": negative,
                "train_unknown_excluded": int(np.sum(~known_train)),
                "validation_positive": int(np.sum(validation_y[:, index] == 1)),
                "validation_provisional_negative": int(np.sum(validation_y[:, index] == 0)),
                "validation_unknown_excluded": int(np.sum(validation_y[:, index] == -1)),
                "low_training_positive_support": positive < MIN_SUPPORT_WARNING,
                "threshold": THRESHOLD,
            }
            if not positive or not negative:
                classifiers[label] = None
                label_reports[label] = {**metadata, "status": "skipped_missing_training_class", "metrics": None}
                print(f"[{index + 1}/23] {label}: skipped, missing known training class", flush=True)
                continue

            classifier = LogisticRegression(**CONFIG["classifier"])
            tick = time.perf_counter()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                classifier.fit(train_x[known_train], target)
            if any(issubclass(item.category, ConvergenceWarning) for item in caught):
                raise ValueError(f"Classifier did not converge for {label}. Inspect configuration before using results.")
            fit_seconds += time.perf_counter() - tick
            tick = time.perf_counter()
            positive_column = list(classifier.classes_).index(1)
            scores[:, index] = classifier.predict_proba(validation_x)[:, positive_column]
            inference_seconds += time.perf_counter() - tick
            if not np.all(np.isfinite(scores[:, index])):
                raise ValueError(f"Non-finite probabilities for {label}.")
            classifiers[label] = classifier
            known_validation = validation_y[:, index] != -1
            truth = validation_y[known_validation, index]
            probabilities = scores[known_validation, index]
            metrics = counts_and_metrics(truth, probabilities >= THRESHOLD)
            metrics["average_precision"] = float(average_precision_score(truth, probabilities)) if (
                np.any(truth == 1) and np.any(truth == 0)
            ) else None
            label_reports[label] = {**metadata, "status": "trained_experimental", "metrics": metrics}
            print(f"[{index + 1}/23] {label}: positive={positive}, negative={negative}, unknown excluded={metadata['train_unknown_excluded']}", flush=True)

        available = np.isfinite(scores)
        known = validation_y != -1
        evaluated = known & available
        if not np.any(evaluated):
            raise ValueError("No validation article/label pairs can be evaluated.")
        predicted = scores >= THRESHOLD
        micro = counts_and_metrics(validation_y[evaluated], predicted[evaluated])
        macro_values = [row["metrics"]["f1"] for row in label_reports.values()
                        if row["metrics"] is not None and row["validation_positive"] > 0
                        and row["metrics"]["f1"] is not None]
        fully_known_and_scored = np.all(evaluated, axis=1)
        fully_known_count = int(np.sum(fully_known_and_scored))
        exact_match = float(np.mean(np.all(
            predicted[fully_known_and_scored] == validation_y[fully_known_and_scored], axis=1
        ))) if fully_known_count else None
        environment = {"python": platform.python_version(), **{
            name: importlib.metadata.version(name) for name in ("numpy", "scipy", "scikit-learn", "joblib")
        }}
        summary = {
            "trained_classifiers": sum(model is not None for model in classifiers.values()),
            "skipped_classifiers": sum(model is None for model in classifiers.values()),
            "evaluated_known_pairs": int(np.sum(evaluated)),
            "validation_unknown_pairs_excluded": int(np.sum(~known)),
            "known_pairs_without_classifier": int(np.sum(known & ~available)),
            "validation_micro": micro,
            "validation_macro_f1_positive_supported_labels": float(np.mean(macro_values)) if macro_values else None,
            "macro_f1_label_count": len(macro_values),
            "fully_known_and_scored_articles": fully_known_count,
            "exact_match_fully_known_and_scored_articles": exact_match,
            "tfidf_features": len(vectorizer.vocabulary_),
            "feature_build_seconds": feature_seconds,
            "classifier_fit_seconds": fit_seconds,
            "validation_predict_seconds_excluding_vectorization": inference_seconds,
        }
        lineage = {
            "input_dataset_sha256": manifest["input_dataset_sha256"],
            "split_manifest_sha256": file_hash(manifest_path),
            "train_sha256": manifest["splits"]["train"]["sha256"],
            "validation_sha256": manifest["splits"]["validation"]["sha256"],
            "preprocessing_version": manifest["input_preprocessing_version"],
            "label_source": manifest["label_source"],
        }
        bundle = {
            "model_version": MODEL_VERSION, "vectorizer": vectorizer,
            "classifiers": classifiers, "labels": list(LABEL_NAMES),
            "thresholds": {label: THRESHOLD for label in LABEL_NAMES},
            "config": CONFIG, "lineage": lineage, "environment": environment,
            "label_training_support": {label: {key: value for key, value in metadata.items()
                                                if key.startswith("train_") or key == "low_training_positive_support"}
                                       for label, metadata in label_reports.items()},
            "deployment_status": "experimental_not_promoted",
        }
        predictions = []
        for row_index, row in enumerate(validation):
            predictions.append({
                "news_id": row["news_id"], "excel_row": row["excel_row"],
                "title": row["title"], "text": row["text"],
                "reference_shared_labels": row["shared_labels"],
                "reference_label_values": row["label_values"],
                "predicted_labels": [label for index, label in enumerate(LABEL_NAMES)
                                     if available[row_index, index] and predicted[row_index, index]],
                "raw_probability_by_label": {
                    label: float(scores[row_index, index]) if available[row_index, index] else None
                    for index, label in enumerate(LABEL_NAMES)
                },
            })
        args.output_dir.mkdir(parents=True, exist_ok=True)
        model_hash = save_model(args.output_dir / "model.joblib", bundle)
        predictions_hash = write_atomic(args.output_dir / "validation_predictions.jsonl", json_lines(predictions))
        report = {
            "schema_version": 1, "model_version": MODEL_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "labels": list(LABEL_NAMES), "config": CONFIG, "environment": environment,
            "lineage": lineage, "train_news": len(train), "validation_news": len(validation),
            "summary": summary, "per_label": label_reports,
            "artifacts": {"model_sha256": model_hash, "validation_predictions_sha256": predictions_hash},
            "elapsed_seconds": time.perf_counter() - start,
            "limitations": [
                "Validation measures agreement with silver consensus targets, not human-validated VV accuracy.",
                "Unknown targets are excluded per label, so metrics describe the known subset only.",
                "Threshold 0.5 is an untuned initial baseline; probabilities are not calibrated guarantees.",
                "Low-support classifiers are experimental and cannot establish reliable rare-label performance.",
                "Macro-F1 excludes labels without positive validation support; count is reported.",
                "Exact match is restricted to fully known, fully scored articles and is not population accuracy.",
                "Test article contents were not read; test remains reserved for final evaluation.",
                "This run is not approved for automatic production risk decisions.",
            ],
        }
        write_atomic(args.output_dir / "training_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("\nTRAINING COMPLETED", flush=True)
        print(f"Trained classifiers: {summary['trained_classifiers']}/23")
        print(f"Validation micro precision: {format_metric(micro['precision'])}")
        print(f"Validation micro recall: {format_metric(micro['recall'])}")
        print(f"Validation micro F1: {format_metric(micro['f1'])}")
        print(f"Validation macro F1 (positive-supported labels only): {format_metric(summary['validation_macro_f1_positive_supported_labels'])}")
        print(f"Macro F1 label count: {summary['macro_f1_label_count']}/23")
        print(f"Validation unknown pairs excluded: {summary['validation_unknown_pairs_excluded']}")
        print(f"Elapsed seconds: {report['elapsed_seconds']:.1f}")
        print("Test contents used: NO")
        print(f"Files saved to: {args.output_dir}")
        print("Metrics compare against consensus labels, not human-reviewed ground truth.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
