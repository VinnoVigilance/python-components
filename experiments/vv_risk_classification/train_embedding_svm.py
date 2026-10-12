"""Fit LinearSVC heads on verified MiniLM embeddings already stored on disk.

No model download, text encoding, encoder fine-tuning, or test reads. The
representation and SVM settings are preserved from the previous experiments.
Unknown (-1) targets are excluded independently for each label.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time
import warnings

import joblib
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.metrics import average_precision_score
from sklearn.svm import LinearSVC

try:
    from audit_dataset import LABEL_NAMES, file_hash
    from prepare_dataset import json_lines, write_atomic
    from train_tfidf_lr import load_partition, counts_and_metrics, save_model, format_metric
    from train_tfidf_svm import CONFIG as TFIDF_SVM_CONFIG
except ModuleNotFoundError as error:
    if error.name not in {"audit_dataset", "prepare_dataset", "train_tfidf_lr", "train_tfidf_svm"}:
        raise
    raise SystemExit("ERROR: put this file alongside the previous experiment scripts.") from error


MODEL_VERSION = "minilm-chunked-linear-svm-silver-v1"
THRESHOLD = 0.0
CLASSIFIER_CONFIG = deepcopy(TFIDF_SVM_CONFIG["classifier"])


def load_cached_features(directory: Path, source: dict, lineage: dict,
                         train: list[dict], validation: list[dict]) -> tuple[np.ndarray, np.ndarray, str]:
    path = directory / "embedding_cache.joblib"
    digest = file_hash(path)
    if digest != source["artifacts"]["embedding_cache_sha256"]:
        raise ValueError("Embedding cache is changed or does not belong to this run.")
    cache = joblib.load(path)
    if cache["lineage"] != lineage or cache["config"] != source["config"]:
        raise ValueError("Embedding cache configuration or data lineage differs.")
    features = []
    for name, rows in (("train", train), ("validation", validation)):
        if cache[f"{name}_news_ids"] != [r["news_id"] for r in rows]:
            raise ValueError(f"Cached {name} ID order differs from the frozen split.")
        x = cache[f"{name}_embeddings"]
        if not isinstance(x, np.ndarray) or x.shape != (len(rows), 384) or x.dtype.kind != "f":
            raise ValueError(f"Invalid cached {name} embedding matrix.")
        if not np.all(np.isfinite(x)) or not np.allclose(np.linalg.norm(x, axis=1), 1, atol=0.0001):
            raise ValueError(f"Cached {name} features are not finite, normalized embeddings.")
        features.append(x)
    return features[0], features[1], digest


def main() -> int:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits-dir", type=Path, default=base / "data" / "processed" / "splits")
    parser.add_argument("--embedding-dir", type=Path, default=base / "outputs" / "embedding_lr_baseline_v1")
    parser.add_argument("--tfidf-svm-dir", type=Path, default=base / "outputs" / "tfidf_svm_baseline_v1")
    parser.add_argument("--output-dir", type=Path, default=base / "outputs" / "embedding_svm_baseline_v1")
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
        baselines = {}
        for name, directory, version in (("embedding_lr", args.embedding_dir, "minilm-chunked-lr-silver-v1"),
                                         ("tfidf_svm", args.tfidf_svm_dir, "tfidf-linear-svm-silver-v1")):
            path = directory / "training_report.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            if report["model_version"] != version or report["labels"] != list(LABEL_NAMES) or report["lineage"] != lineage:
                raise ValueError(f"Baseline {name} version, labels, or data lineage differs.")
            baselines[name] = {"report": report, "report_sha256": file_hash(path)}
        source = baselines["embedding_lr"]["report"]
        if source["config"]["encoder"]["dimension"] != 384 or source["config"]["encoder"]["fine_tuned"]:
            raise ValueError("Expected frozen 384-dimensional MiniLM representation.")
        if source["config"]["representation"]["version"] != "full-text-character-spans-token-weighted-mean-v1":
            raise ValueError("Unexpected chunk pooling configuration.")
        if baselines["tfidf_svm"]["report"]["config"]["classifier"] != CLASSIFIER_CONFIG:
            raise ValueError("SVM configuration differs from the previous TF-IDF/SVM experiment.")
        tick = time.perf_counter()
        train_x, validation_x, cache_hash = load_cached_features(args.embedding_dir, source, lineage, train, validation)
        # Preserve the exact encoder identity needed for future inference.
        encoder_files = source["artifacts"]["encoder_file_sha256"]
        if not encoder_files:
            raise ValueError("Missing saved encoder identity.")
        encoder_dir = (args.embedding_dir / "encoder").resolve()
        for relative, digest in encoder_files.items():
            path = (encoder_dir / relative).resolve()
            if not path.is_relative_to(encoder_dir) or file_hash(path) != digest:
                raise ValueError("Saved encoder is missing or changed.")
        cache_load_seconds = time.perf_counter() - tick
        print(f"Train news: {len(train)}", flush=True)
        print(f"Validation news: {len(validation)}", flush=True)
        print("Verified cached embeddings: YES; embedding dimension: 384", flush=True)
        print("Re-encoding/chunk processing: NO", flush=True)

        scores = np.full(validation_y.shape, np.nan, dtype=np.float64)
        classifiers, per_label = {}, {}
        fit_seconds = predict_seconds = 0.0
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
            for b in baselines.values():
                if any(b["report"]["per_label"][label][k] != support[k] for k in support if k.startswith(("train_", "validation_"))):
                    raise ValueError(f"Baseline target support differs for {label}.")
            if not positive or not negative:
                classifiers[label] = None
                per_label[label] = {**support, "status": "skipped_missing_training_class", "metrics": None}
                continue
            classifier = LinearSVC(**CLASSIFIER_CONFIG)
            tick = time.perf_counter()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                classifier.fit(train_x[known_train], target)
            if any(issubclass(w.category, ConvergenceWarning) for w in caught):
                raise ValueError(f"SVM did not converge for {label}.")
            fit_seconds += time.perf_counter() - tick
            if list(classifier.classes_) != [0, 1]:
                raise ValueError(f"Unexpected SVM score direction for {label}.")
            tick = time.perf_counter()
            scores[:, index] = classifier.decision_function(validation_x)
            predict_seconds += time.perf_counter() - tick
            if not np.all(np.isfinite(scores[:, index])):
                raise ValueError(f"Non-finite scores for {label}.")
            classifiers[label] = classifier
            mask = validation_y[:, index] != -1
            truth, label_scores = validation_y[mask, index], scores[mask, index]
            metrics = counts_and_metrics(truth, label_scores > THRESHOLD)
            metrics["average_precision"] = float(average_precision_score(truth, label_scores)) if (
                np.any(truth == 1) and np.any(truth == 0)) else None
            per_label[label] = {**support, "status": "trained_experimental", "metrics": metrics}
            print(f"[{index + 1}/23] {label}: positive={positive}, negative={negative}, unknown excluded={support['train_unknown_excluded']}", flush=True)

        known, available = validation_y != -1, np.isfinite(scores)
        evaluated = known & available
        if not np.any(evaluated):
            raise ValueError("No validation pairs can be evaluated.")
        predicted = scores > THRESHOLD
        micro = counts_and_metrics(validation_y[evaluated], predicted[evaluated])
        f1s = [r["metrics"]["f1"] for r in per_label.values() if r["metrics"] is not None
               and r["validation_positive"] > 0 and r["metrics"]["f1"] is not None]
        fully_known = np.all(evaluated, axis=1)
        summary = {
            "trained_classifiers": sum(c is not None for c in classifiers.values()),
            "skipped_classifiers": sum(c is None for c in classifiers.values()),
            "evaluated_known_pairs": int(np.sum(evaluated)), "validation_unknown_pairs_excluded": int(np.sum(~known)),
            "known_pairs_without_classifier": int(np.sum(known & ~available)), "validation_micro": micro,
            "validation_macro_f1_positive_supported_labels": float(np.mean(f1s)) if f1s else None,
            "macro_f1_label_count": len(f1s), "fully_known_and_scored_articles": int(np.sum(fully_known)),
            "exact_match_fully_known_and_scored_articles": float(np.mean(np.all(predicted[fully_known] == validation_y[fully_known], axis=1))) if np.any(fully_known) else None,
            "embedding_dimension": 384, "cache_verification_seconds": cache_load_seconds,
            "classifier_fit_seconds": fit_seconds, "validation_predict_seconds_excluding_encoding": predict_seconds,
            "encoder_run_this_step": False,
        }
        comparisons = {}
        for name, b in baselines.items():
            old = b["report"]
            for key in ("evaluated_known_pairs", "known_pairs_without_classifier", "macro_f1_label_count"):
                if old["summary"][key] != summary[key]:
                    raise ValueError(f"Evaluation coverage differs from {name}: {key}.")
            ap_labels = [l for l in LABEL_NAMES if per_label[l]["metrics"] is not None and old["per_label"][l]["metrics"] is not None
                         and per_label[l]["metrics"]["average_precision"] is not None
                         and old["per_label"][l]["metrics"]["average_precision"] is not None]
            comparisons[name] = {
                "baseline_model_version": old["model_version"], "baseline_report_sha256": b["report_sha256"],
                "baseline_micro": old["summary"]["validation_micro"], "embedding_svm_micro": micro,
                "baseline_macro_f1": old["summary"]["validation_macro_f1_positive_supported_labels"],
                "embedding_svm_macro_f1": summary["validation_macro_f1_positive_supported_labels"],
                "average_precision_labels": ap_labels,
                "baseline_macro_average_precision": float(np.mean([old["per_label"][l]["metrics"]["average_precision"] for l in ap_labels])) if ap_labels else None,
                "embedding_svm_macro_average_precision": float(np.mean([per_label[l]["metrics"]["average_precision"] for l in ap_labels])) if ap_labels else None,
                "interpretation": "Same frozen known targets; default decision rules, not matched precision operating points. No winner selected.",
            }
        config = {
            "encoder": deepcopy(source["config"]["encoder"]),
            "representation": deepcopy(source["config"]["representation"]),
            "classifier": CLASSIFIER_CONFIG, "initial_threshold": THRESHOLD,
            "prediction_rule": "decision_score > threshold", "score_type": "uncalibrated_signed_margin",
            "unknown_target": -1, "cached_embeddings_reused": True,
        }
        environment = {"python": platform.python_version(), **{n: importlib.metadata.version(n) for n in ("numpy", "scipy", "scikit-learn", "joblib")}}
        representation_source = {
            "embedding_model_version": source["model_version"],
            "embedding_report_sha256": baselines["embedding_lr"]["report_sha256"],
            "embedding_cache_sha256": cache_hash, "encoder_file_sha256": encoder_files,
            "source_encoding_environment": source["environment"],
        }
        bundle = {
            "model_version": MODEL_VERSION, "classifiers": classifiers, "labels": list(LABEL_NAMES),
            "thresholds": {l: THRESHOLD for l in LABEL_NAMES}, "config": config,
            "encoder_relative_path": Path(os.path.relpath(encoder_dir, args.output_dir.resolve())).as_posix(),
            "encoder_file_sha256": encoder_files, "representation_function": "train_embedding_lr.build_embeddings",
            "representation_source": representation_source, "lineage": lineage, "environment": environment,
            "label_training_support": {l: {k: v for k, v in r.items() if k.startswith("train_") or k == "low_training_positive_support"} for l, r in per_label.items()},
            "deployment_status": "experimental_not_promoted",
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        model_hash = save_model(args.output_dir / "model.joblib", bundle)
        predictions = [{
            "news_id": r["news_id"], "excel_row": r["excel_row"], "title": r["title"], "text": r["text"],
            "reference_shared_labels": r["shared_labels"], "reference_label_values": r["label_values"],
            "predicted_labels": [l for j, l in enumerate(LABEL_NAMES) if available[i, j] and predicted[i, j]],
            "score_type": config["score_type"],
            "raw_decision_score_by_label": {l: float(scores[i, j]) if available[i, j] else None for j, l in enumerate(LABEL_NAMES)},
        } for i, r in enumerate(validation)]
        prediction_hash = write_atomic(args.output_dir / "validation_predictions.jsonl", json_lines(predictions))
        report = {
            "schema_version": 1, "model_version": MODEL_VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "labels": list(LABEL_NAMES), "config": config, "environment": environment, "lineage": lineage,
            "train_news": len(train), "validation_news": len(validation), "summary": summary, "per_label": per_label,
            "comparison_to_baselines": comparisons, "representation_source": representation_source,
            "artifacts": {"model_sha256": model_hash, "validation_predictions_sha256": prediction_hash,
                          "script_sha256": file_hash(Path(__file__))},
            "elapsed_seconds": time.perf_counter() - started,
            "limitations": [
                "Metrics describe silver-label agreement on known pairs, not human-reviewed accuracy.",
                "Signed SVM margins are not probabilities, risk severity, or comparable across labels.",
                "Chunk pooling and frozen encoder limitations persist; no text was re-encoded in this step.",
                "Current elapsed time excludes the previous embedding construction cost, which applies to new unseen articles.",
                "Rare-label metrics remain unstable with small support; macro-F1 excludes labels without positive validation support.",
                "Default thresholds are not matched precision operating points; repeated validation comparisons can overfit model selection.",
                "Model heads reference the saved encoder in the original embedding run; preserve both directories.",
                "Test contents were not read. Experimental model; no production promotion or model winner selected.",
            ],
        }
        write_atomic(args.output_dir / "training_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("\nEMBEDDING SVM TRAINING COMPLETED")
        print(f"Trained classifiers: {summary['trained_classifiers']}/23")
        for name in ("precision", "recall", "f1"):
            print(f"Validation micro {name}: {format_metric(micro[name])}")
        print(f"Validation macro F1: {format_metric(summary['validation_macro_f1_positive_supported_labels'])}")
        print(f"Macro F1 label count: {summary['macro_f1_label_count']}/23")
        for name, c in comparisons.items():
            print(f"Micro F1 ({name} -> embedding SVM): {format_metric(c['baseline_micro']['f1'])} -> {format_metric(micro['f1'])}")
            print(f"Macro average precision ({name} -> embedding SVM): {format_metric(c['baseline_macro_average_precision'])} -> {format_metric(c['embedding_svm_macro_average_precision'])} ({len(c['average_precision_labels'])}/23 labels)")
        print(f"Validation unknown pairs excluded: {summary['validation_unknown_pairs_excluded']}")
        print("Re-encoding/chunk processing: NO")
        print("Score type: signed margin, NOT probability")
        print(f"Elapsed seconds (cached features): {report['elapsed_seconds']:.1f}")
        print("Test contents used: NO")
        print(f"Files saved to: {args.output_dir}")
        print("Consensus agreement only. Default thresholds; no winner selected.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
