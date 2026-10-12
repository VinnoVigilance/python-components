"""Analyze saved VV SVM validation margins; never train or promote a model."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

MODEL_VERSION = "tfidf-linear-svm-silver-v1"
MANIFEST_SHA = "85d541bbedce5e6b7ab90d08752fa54e6e044f810a2ad74770f298bbef67f0e7"
VALIDATION_SHA = "19d7efb8583f56d10f6f5ad2ed09ab2c7b18e9a4e9dd2bdcc7696455356fb13e"
LABELS_SHA = "4b955a187097dd9b66bde18768b9f48efd3d94c7d1d7ea384c9c08a1f09805d4"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), sha(raw)


def read_jsonl(path):
    raw = path.read_bytes()
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()], sha(raw)


def metrics(tp, fp, fn, tn):
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        "predicted_positive_known_pairs": tp + fp,
    }


def evaluate(known_pairs, threshold):
    tp = fp = fn = tn = 0
    for score, target in known_pairs:
        if score > threshold:
            tp += target == 1
            fp += target == 0
        else:
            fn += target == 1
            tn += target == 0
    return metrics(tp, fp, fn, tn)


def curve(known_pairs):
    """Enumerate attainable operating points, respecting tied scores and score > t."""
    ordered = sorted(known_pairs, reverse=True)
    positives = sum(target == 1 for _, target in ordered)
    negatives = len(ordered) - positives
    tp = fp = index = 0
    points = []
    while index < len(ordered):
        score = ordered[index][0]
        while index < len(ordered) and ordered[index][0] == score:
            tp += ordered[index][1] == 1
            fp += ordered[index][1] == 0
            index += 1
        threshold = math.nextafter(score, -math.inf)
        require(math.isfinite(threshold), "Non-finite candidate threshold.")
        points.append({"threshold": threshold, "metrics": metrics(tp, fp, positives - tp, negatives - fp)})
    points.append({"threshold": 0.0, "metrics": evaluate(known_pairs, 0.0)})
    return points


def aggregate(per_label, selected_labels, field):
    sums = {key: sum(per_label[label][field][key] for label in selected_labels) for key in ("tp", "fp", "fn", "tn")}
    f1s = [per_label[label][field]["f1"] for label in selected_labels if per_label[label]["validation_positive"] > 0]
    return {
        "micro": metrics(**sums),
        "macro_f1_positive_supported_labels": sum(f1s) / len(f1s) if f1s else None,
        "macro_f1_label_count": len(f1s),
        "evaluated_known_pairs": sum(sums.values()),
        "unknown_pairs_excluded": sum(per_label[label]["validation_unknown"] for label in selected_labels),
        "predicted_positive_unknown_pairs": sum(per_label[label][field + "_unknown_positive"] for label in selected_labels),
    }


def check_metrics(actual, expected, label):
    for key in ("tp", "fp", "fn", "tn", "precision", "recall", "f1"):
        a, b = actual[key], expected[key]
        require((a is None and b is None) or (
            a is not None and b is not None and math.isclose(a, b, abs_tol=1e-10, rel_tol=1e-10)
        ), f"Saved baseline metrics do not match for {label}: {key}.")


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, default=here / "outputs/tfidf_svm_baseline_v1")
    parser.add_argument("--splits-dir", type=Path, default=here / "data/processed/splits")
    parser.add_argument("--labels-path", type=Path, default=here / "data/processed/labels.json")
    parser.add_argument("--output-root", type=Path, default=here / "outputs/tfidf_svm_threshold_analysis_v1")
    parser.add_argument("--precision-floor", type=float, default=0.95)
    parser.add_argument("--min-predicted-positive", type=int, default=10)
    args = parser.parse_args()
    require(math.isfinite(args.precision_floor) and 0 < args.precision_floor <= 1, "Invalid precision floor.")
    require(args.min_predicted_positive >= 1, "Minimum prediction count must be positive.")

    labels, labels_hash = read_json(args.labels_path)
    manifest, manifest_hash = read_json(args.splits_dir / "split_manifest.json")
    validation, validation_hash = read_jsonl(args.splits_dir / "validation.jsonl")
    baseline, baseline_hash = read_json(args.baseline_dir / "training_report.json")
    predictions, predictions_hash = read_jsonl(args.baseline_dir / "validation_predictions.jsonl")

    require(labels_hash == LABELS_SHA, "Frozen labels file mismatch.")
    require(manifest_hash == MANIFEST_SHA, "Frozen manifest mismatch.")
    require(validation_hash == VALIDATION_SHA, "Frozen validation mismatch.")
    require(len(labels) == 23 and len(set(labels)) == 23, "Expected 23 unique labels.")
    require(labels == manifest["labels"] == baseline["labels"], "Label order mismatch.")
    require(baseline["model_version"] == MODEL_VERSION, "Expected the original TF-IDF SVM baseline.")
    require(baseline["lineage"]["split_manifest_sha256"] == manifest_hash, "Baseline manifest lineage mismatch.")
    require(baseline["lineage"]["validation_sha256"] == validation_hash, "Baseline validation lineage mismatch.")
    require(baseline["lineage"]["input_dataset_sha256"] == manifest["input_dataset_sha256"], "Dataset lineage mismatch.")
    require(baseline["lineage"]["train_sha256"] == manifest["splits"]["train"]["sha256"], "Training metadata lineage mismatch.")
    require(predictions_hash == baseline["artifacts"]["validation_predictions_sha256"], "Prediction artifact hash mismatch.")
    require(baseline["config"]["score_type"] == "uncalibrated_signed_margin", "Unexpected score type.")
    require(baseline["config"]["prediction_rule"] == "decision_score > threshold", "Unexpected prediction rule.")
    require(len(validation) == len(predictions) == 728, "Expected 728 validation records.")
    ids = [row["news_id"] for row in validation]
    require(ids == manifest["splits"]["validation"]["news_ids"], "Validation ID order mismatch.")
    require(ids == [row["news_id"] for row in predictions], "Prediction ID order mismatch.")
    require(len(set(ids)) == len(ids), "Duplicate validation IDs.")

    by_label = {label: {"known": [], "unknown_scores": []} for label in labels}
    for row, prediction in zip(validation, predictions):
        targets = row["label_values"]
        require(len(targets) == 23 and all(type(x) is int and x in (-1, 0, 1) for x in targets), "Invalid target encoding.")
        require(targets == prediction["reference_label_values"], f"Target mismatch: {row['news_id']}.")
        require(row["text"] == prediction["text"], f"Prediction input mismatch: {row['news_id']}.")
        require(prediction["score_type"] == "uncalibrated_signed_margin", "Prediction score type mismatch.")
        scores = prediction["raw_decision_score_by_label"]
        require(set(scores) == set(labels), "Score label set mismatch.")
        expected_positive = []
        for index, label in enumerate(labels):
            score = scores[label]
            require(type(score) in (float, int) and math.isfinite(score), f"Missing/non-finite margin: {label}.")
            if score > 0:
                expected_positive.append(label)
            if targets[index] == -1:
                by_label[label]["unknown_scores"].append(score)
            else:
                by_label[label]["known"].append((score, targets[index]))
        require(expected_positive == prediction["predicted_labels"], "Saved default prediction rule mismatch.")

    per_label = {}
    primary = []
    for label in labels:
        saved = baseline["per_label"][label]
        require(saved["threshold"] == 0.0 and saved["metrics"] is not None, f"Expected original trained default classifier: {label}.")
        known = by_label[label]["known"]
        unknown_scores = by_label[label]["unknown_scores"]
        positives = sum(target == 1 for _, target in known)
        negatives = len(known) - positives
        require(positives == saved["validation_positive"] == manifest["support_by_split"]["validation"][label]["positive"], f"Validation positive support mismatch: {label}.")
        require(negatives == saved["validation_provisional_negative"], f"Validation negative support mismatch: {label}.")
        require(len(unknown_scores) == saved["validation_unknown_excluded"], f"Unknown support mismatch: {label}.")
        train_positive = manifest["support_by_split"]["train"][label]["positive"]
        require(train_positive == saved["train_positive"], f"Training support metadata mismatch: {label}.")
        default = evaluate(known, 0.0)
        check_metrics(default, saved["metrics"], label)
        eligible = train_positive >= 20 and positives >= 10
        threshold = 0.0
        best_f1 = precision_candidate = None
        status = "rare_label_unchanged_not_validated"
        if eligible:
            primary.append(label)
            points = curve(known)
            best_f1 = max(points, key=lambda p: (p["metrics"]["f1"] or 0, p["metrics"]["precision"] or 0, p["threshold"]))
            feasible = [p for p in points if p["metrics"]["predicted_positive_known_pairs"] >= args.min_predicted_positive and (p["metrics"]["precision"] or 0) + 1e-12 >= args.precision_floor]
            if feasible:
                precision_candidate = max(feasible, key=lambda p: (p["metrics"]["recall"] or 0, p["metrics"]["precision"] or 0, p["threshold"]))
                threshold = precision_candidate["threshold"]
                status = "precision_threshold_proposed"
            else:
                status = "no_feasible_precision_threshold_default_retained"
        proposed = evaluate(known, threshold)
        per_label[label] = {
            "eligible_primary": eligible, "train_positive": train_positive,
            "validation_positive": positives, "validation_provisional_negative": negatives,
            "validation_unknown": len(unknown_scores), "status": status,
            "default_threshold": 0.0, "proposed_threshold": threshold,
            "default_metrics": default, "proposed_metrics": proposed,
            "default_metrics_unknown_positive": sum(score > 0 for score in unknown_scores),
            "proposed_metrics_unknown_positive": sum(score > threshold for score in unknown_scores),
            "precision_candidate": precision_candidate,
            "max_f1_candidate_diagnostic_only": best_f1,
        }

    require(len(primary) == 14, "Fixed PRIMARY_14 support policy mismatch.")
    default_full = aggregate(per_label, labels, "default_metrics")
    check_metrics(default_full["micro"], baseline["summary"]["validation_micro"], "ORIGINAL_23 micro")
    require(default_full["evaluated_known_pairs"] == baseline["summary"]["evaluated_known_pairs"], "Known pair count mismatch.")
    require(default_full["unknown_pairs_excluded"] == baseline["summary"]["validation_unknown_pairs_excluded"], "Unknown pair count mismatch.")
    require(math.isclose(default_full["macro_f1_positive_supported_labels"], baseline["summary"]["validation_macro_f1_positive_supported_labels"], abs_tol=1e-10), "Baseline macro F1 mismatch.")

    summaries = {
        scope: {
            "default": aggregate(per_label, selected, "default_metrics"),
            "proposed": aggregate(per_label, selected, "proposed_metrics"),
        }
        for scope, selected in (("PRIMARY_14", primary), ("ORIGINAL_23", labels))
    }
    policy = {
        "precision_floor_on_known_silver_pairs": args.precision_floor,
        "min_predicted_positive_known_pairs": args.min_predicted_positive,
        "objective": "maximize per-label recall subject to empirical precision floor and minimum prediction count",
        "tie_break": "higher precision, then higher threshold",
        "eligible_labels": "train_positive >= 20 and validation_positive >= 10",
        "support_rules_are_practical_heuristics_not_statistical_guarantees": True,
        "ineligible_or_infeasible_label_policy": "retain default threshold; flag limitations explicitly",
        "unknown_target": -1, "unknown_pairs_excluded_from_metrics": True,
        "rare_labels_automatically_approved": False,
        "business_policy_approved": False,
    }
    created = datetime.now(timezone.utc)
    report = {
        "schema_version": 1, "analysis_version": "vv-svm-threshold-analysis-v1",
        "created_at_utc": created.isoformat(), "base_model_version": MODEL_VERSION,
        "labels": labels, "primary_labels": primary,
        "rare_labels": [label for label in labels if label not in primary],
        "policy": policy, "summary": summaries, "per_label": per_label,
        "lineage": {**baseline["lineage"], "labels_sha256": labels_hash,
                    "baseline_report_sha256": baseline_hash,
                    "validation_predictions_sha256": predictions_hash,
                    "analysis_script_sha256": sha(Path(__file__).read_bytes())},
        "operations": {"training": False, "new_inference": False, "model_artifact_modified": False,
                       "train_contents_read": False, "test_contents_read": False, "production_promotion": False},
        "limitations": [
            "Metrics measure agreement with consensus silver labels, not human-reviewed accuracy.",
            "Targets -1 are excluded per label; predicted positives on unknown pairs are counted separately, not called false positives.",
            "Threshold selection and proposed metrics use the same validation records; these are selection results, not independent evaluation.",
            "An empirical precision floor does not guarantee precision on new news, especially with small support.",
            "The nine rare labels retain default thresholds for comparison only; retaining them is not release approval.",
            "No probability calibration, evidence extraction, severity estimation, or model promotion is performed.",
            "Previously fixed PRIMARY_14 support policy is retained; repeated validation selection can overfit.",
        ],
    }
    proposals = {
        "schema_version": 1, "base_model_version": MODEL_VERSION,
        "status": "validation_proposal_not_approved_for_production",
        "labels": labels, "score_type": "uncalibrated_signed_margin",
        "prediction_rule": "decision_score > threshold", "policy": policy,
        "thresholds": {label: per_label[label]["proposed_threshold"] for label in labels},
        "label_status": {label: per_label[label]["status"] for label in labels},
        "lineage": report["lineage"],
    }

    output = args.output_root / (created.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    for name, content in (("threshold_analysis.json", report), ("proposed_thresholds.json", proposals)):
        (output / name).write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    with (output / "per_label_thresholds.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["label", "eligible_primary", "train_positive", "validation_positive", "status", "default_threshold", "proposed_threshold", "default_precision", "proposed_precision", "default_recall", "proposed_recall", "default_f1", "proposed_f1", "proposed_predicted_positive_known_pairs", "default_positive_unknown_pairs", "proposed_positive_unknown_pairs", "max_f1_diagnostic_threshold", "max_f1_diagnostic_f1"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for label, item in per_label.items():
            best = item["max_f1_candidate_diagnostic_only"]
            csv_row = {"label": label, **{key: item[key] for key in ("eligible_primary", "train_positive", "validation_positive", "status", "default_threshold", "proposed_threshold")}}
            for metric in ("precision", "recall", "f1"):
                for kind in ("default", "proposed"):
                    csv_row[kind + "_" + metric] = item[kind + "_metrics"][metric]
            csv_row.update({
                "proposed_predicted_positive_known_pairs": item["proposed_metrics"]["predicted_positive_known_pairs"],
                "default_positive_unknown_pairs": item["default_metrics_unknown_positive"],
                "proposed_positive_unknown_pairs": item["proposed_metrics_unknown_positive"],
                "max_f1_diagnostic_threshold": best["threshold"] if best else None,
                "max_f1_diagnostic_f1": best["metrics"]["f1"] if best else None,
            })
            writer.writerow(csv_row)
    shutil.copyfile(Path(__file__), output / Path(__file__).name)

    print("SVM THRESHOLD ANALYSIS COMPLETED")
    print("Baseline hashes, label order and default metrics: OK")
    print(f"Precision floor: {args.precision_floor}; minimum known positive predictions: {args.min_predicted_positive}")
    print("Primary labels: 14; rare labels unchanged and not approved: 9")
    print("Thresholds proposed:", sum(per_label[label]["status"] == "precision_threshold_proposed" for label in primary))
    print("No feasible threshold; default retained:", sum(per_label[label]["status"] == "no_feasible_precision_threshold_default_retained" for label in primary))
    for scope, summary in summaries.items():
        print("\n" + scope)
        for key in ("precision", "recall", "f1"):
            before, after = summary["default"]["micro"][key], summary["proposed"]["micro"][key]
            print(f"Micro {key}: {before:.4f} -> {after:.4f}")
        print("Macro F1: %.4f -> %.4f" % (summary["default"]["macro_f1_positive_supported_labels"], summary["proposed"]["macro_f1_positive_supported_labels"]))
        print("Predicted positive unknown pairs:", summary["default"]["predicted_positive_unknown_pairs"], "->", summary["proposed"]["predicted_positive_unknown_pairs"])
    print("\nTraining / new inference / train/test contents used: NO")
    print("Existing model modified / production promotion: NO")
    print("Validation selection results only; no human accuracy or release approval established.")
    print("Files saved to:", output.resolve())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Threshold analysis failed: {error}", file=sys.stderr)
        sys.exit(1)
