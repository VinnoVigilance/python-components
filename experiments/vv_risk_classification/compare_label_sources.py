"""Compare cached VV predictions with Claude, GPT and masked silver consensus.

No model fitting, inference, threshold selection, train reads or test reads.
Run from the repository root:
    python experiments/vv_risk_classification/compare_label_sources.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from sklearn.metrics import average_precision_score


MODEL_SPECS = {
    "tfidf_lr": ("tfidf_lr_baseline_v1", "training_report.json", "raw_probability_by_label", False),
    "tfidf_svm": ("tfidf_svm_baseline_v1", "training_report.json", "raw_decision_score_by_label", True),
    "embedding_lr": ("embedding_lr_baseline_v1", "training_report.json", "raw_probability_by_label", False),
    "embedding_svm": ("embedding_svm_baseline_v1", "training_report.json", "raw_decision_score_by_label", True),
    "gliclass": ("gliclass_zeroshot_v1", "evaluation_report.json", "raw_score_by_label", False),
}


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path: Path):
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{number}: expected an object")
            rows.append(row)
    return rows


def label_set(value, labels: list[str], context: str) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValueError(f"{context}: expected a list of label names")
    result = set(value)
    if len(result) != len(value) or not result.issubset(labels):
        raise ValueError(f"{context}: duplicate or unknown label")
    return result


def counts(truth: np.ndarray, predicted: np.ndarray, mask: np.ndarray) -> dict:
    truth, predicted = truth[mask].astype(bool), predicted[mask].astype(bool)
    tp = int(np.sum(truth & predicted))
    fp = int(np.sum(~truth & predicted))
    fn = int(np.sum(truth & ~predicted))
    tn = int(np.sum(~truth & ~predicted))
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def evaluate(truth, mask, predicted, scores, labels) -> dict:
    per_label, f1_values, ap_values, f1_labels, ap_labels = {}, [], [], [], []
    for j, name in enumerate(labels):
        known = mask[:, j]
        positive = int(np.sum((truth[:, j] == 1) & known))
        negative = int(np.sum((truth[:, j] == 0) & known))
        metrics = counts(truth[:, j], predicted[:, j], known)
        ap = None
        if positive > 0:
            f1_labels.append(name)
            f1_values.append(metrics["f1"])
        if positive > 0 and negative > 0:
            ap = float(average_precision_score(truth[known, j], scores[known, j]))
            ap_labels.append(name)
            ap_values.append(ap)
        per_label[name] = {
            "reference_positive": positive,
            "reference_negative": negative,
            "excluded_pairs": int(np.sum(~known)),
            "metrics": {**metrics, "average_precision": ap},
        }
    fully_known = np.all(mask, axis=1)
    return {
        "evaluated_pairs": int(np.sum(mask)),
        "excluded_pairs": int(np.sum(~mask)),
        "reference_positive_pairs": int(np.sum((truth == 1) & mask)),
        "micro": counts(truth, predicted, mask),
        "macro_f1_positive_supported_labels": float(np.mean(f1_values)) if f1_values else None,
        "macro_f1_labels": f1_labels,
        "macro_average_precision_both_classes_supported": float(np.mean(ap_values)) if ap_values else None,
        "average_precision_labels": ap_labels,
        "fully_known_articles": int(np.sum(fully_known)),
        "exact_match_fully_known_articles": float(np.mean(np.all(truth[fully_known] == predicted[fully_known], axis=1))) if np.any(fully_known) else None,
        "per_label": per_label,
    }


def load_validation(splits_dir: Path):
    manifest_path = splits_dir / "split_manifest.json"
    manifest = read_json(manifest_path)
    labels = manifest["labels"]
    if len(labels) != 23 or len(set(labels)) != 23 or any(not isinstance(x, str) for x in labels):
        raise ValueError("Frozen manifest must contain exactly 23 unique label names")
    if manifest.get("target_encoding") != {"positive": 1, "provisional_negative": 0, "unknown": -1}:
        raise ValueError("Unexpected frozen target encoding")
    validation_path = splits_dir / "validation.jsonl"
    validation_hash = sha256(validation_path)
    if validation_hash != manifest["splits"]["validation"]["sha256"]:
        raise ValueError("Validation hash differs from the frozen manifest")
    rows = read_jsonl(validation_path)
    ids = [row["news_id"] for row in rows]
    if len(ids) != len(set(ids)) or ids != manifest["splits"]["validation"]["news_ids"]:
        raise ValueError("Validation IDs/order differ from the frozen manifest")
    if len(rows) != manifest["splits"]["validation"]["rows"]:
        raise ValueError("Validation row count differs from the frozen manifest")
    consensus, claude, gpt = [], [], []
    for row in rows:
        cid = label_set(row["claude_labels"], labels, f"{row['news_id']}/claude")
        gid = label_set(row["gpt_labels"], labels, f"{row['news_id']}/gpt")
        values = row["label_values"]
        expected = [1 if name in cid & gid else -1 if name in cid ^ gid else 0 for name in labels]
        if values != expected:
            raise ValueError(f"{row['news_id']}: stored consensus disagrees with original annotators")
        consensus.append(values)
        claude.append([int(name in cid) for name in labels])
        gpt.append([int(name in gid) for name in labels])
    lineage = {
        "input_dataset_sha256": manifest["input_dataset_sha256"],
        "split_manifest_sha256": sha256(manifest_path),
        "train_sha256": manifest["splits"]["train"]["sha256"],  # metadata only
        "validation_sha256": validation_hash,
        "preprocessing_version": manifest["input_preprocessing_version"],
        "label_source": manifest["label_source"],
    }
    return rows, labels, lineage, np.asarray(claude), np.asarray(gpt), np.asarray(consensus)


def load_model(root, key, validation, labels, lineage):
    folder, report_name, score_field, signed_margin = MODEL_SPECS[key]
    report_path = root / folder / report_name
    prediction_path = root / folder / "validation_predictions.jsonl"
    report = read_json(report_path)
    if report["labels"] != labels or report["lineage"] != lineage:
        raise ValueError(f"{key}: model report belongs to a different dataset or split")
    if report["validation_news"] != len(validation):
        raise ValueError(f"{key}: report validation row count mismatch")
    if report.get("test_contents_used", False):
        raise ValueError(f"{key}: report says test contents were used")
    prediction_hash = sha256(prediction_path)
    if prediction_hash != report["artifacts"]["validation_predictions_sha256"]:
        raise ValueError(f"{key}: cached prediction file hash differs from its report")
    saved = read_jsonl(prediction_path)
    indexed = {}
    for row in saved:
        if row["news_id"] in indexed:
            raise ValueError(f"{key}: duplicate cached prediction ID")
        indexed[row["news_id"]] = row
    if set(indexed) != {row["news_id"] for row in validation}:
        raise ValueError(f"{key}: cached predictions do not cover exactly frozen validation IDs")
    predicted, scores, thresholds = [], [], []
    for name in labels:
        threshold = report["per_label"][name]["threshold"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
            raise ValueError(f"{key}: missing/invalid saved threshold for {name}")
        thresholds.append(threshold)
    for source in validation:
        row = indexed[source["news_id"]]
        if row.get("reference_label_values") != source["label_values"]:
            raise ValueError(f"{key}/{source['news_id']}: cached reference targets differ")
        if row.get("text") != source["text"]:
            raise ValueError(f"{key}/{source['news_id']}: cached feature text differs")
        positive = label_set(row["predicted_labels"], labels, f"{key}/{source['news_id']}")
        raw = row[score_field]
        if set(raw) != set(labels):
            raise ValueError(f"{key}: score-label coverage differs")
        vals = [float(raw[name]) for name in labels]
        if any(not math.isfinite(v) for v in vals) or (not signed_margin and any(v < 0 or v > 1 for v in vals)):
            raise ValueError(f"{key}: invalid raw scores")
        binary = [int(name in positive) for name in labels]
        expected = [int(v > t if signed_margin else v >= t) for v, t in zip(vals, thresholds)]
        if binary != expected:
            raise ValueError(f"{key}: predictions differ from original saved decision rule")
        predicted.append(binary)
        scores.append(vals)
    return np.asarray(predicted), np.asarray(scores), report, {
        "model_version": report["model_version"],
        "model_report_sha256": sha256(report_path),
        "validation_predictions_sha256": prediction_hash,
        "saved_thresholds": dict(zip(labels, thresholds)),
    }


def verify_consensus(metrics, original, labels, key):
    micro = original["summary"]["validation_micro"]
    for name in ("tp", "fp", "fn", "tn"):
        if micro[name] != metrics["micro"][name]:
            raise ValueError(f"{key}: recomputed consensus {name} differs from original report")
    for label in labels:
        for name in ("tp", "fp", "fn", "tn"):
            if original["per_label"][label]["metrics"][name] != metrics["per_label"][label]["metrics"][name]:
                raise ValueError(f"{key}: recomputed consensus confusion counts differ for {label}")


def write_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits-dir", type=Path, default=base / "data/processed/splits")
    parser.add_argument("--outputs-root", type=Path, default=base / "outputs")
    parser.add_argument("--output-dir", type=Path, default=base / "outputs/label_source_comparison_v1")
    parser.add_argument("--models", nargs="+", choices=list(MODEL_SPECS), default=list(MODEL_SPECS))
    args = parser.parse_args()
    try:
        if len(set(args.models)) != len(args.models):
            raise ValueError("Duplicate model selection")
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ValueError("Output folder is nonempty; choose a new --output-dir to preserve prior results")
        validation, labels, lineage, claude, gpt, consensus = load_validation(args.splits_dir)
        all_pairs = np.ones(claude.shape, dtype=bool)
        known = consensus != -1
        disagree = claude != gpt
        views = {
            "claude": (claude, all_pairs),
            "gpt": (gpt, all_pairs),
            "consensus_known_pairs": (consensus, known),
        }
        comparisons, provenance = {}, {}
        for key in args.models:
            predicted, scores, original, origin = load_model(args.outputs_root, key, validation, labels, lineage)
            comparisons[key] = {name: evaluate(truth, mask, predicted, scores, labels) for name, (truth, mask) in views.items()}
            verify_consensus(comparisons[key]["consensus_known_pairs"], original, labels, key)
            comparisons[key]["on_annotator_disagreement_pairs"] = {
                "evaluated_pairs": int(np.sum(disagree)),
                "claude": counts(claude, predicted, disagree),
                "gpt": counts(gpt, predicted, disagree),
                "interpretation": "Diagnostic only; favoring either annotator does not establish correctness.",
            }
            provenance[key] = origin
            print(f"Verified cached predictions and original consensus counts: {key}", flush=True)
        common_positive = int(np.sum((claude == 1) & (gpt == 1)))
        union_positive = int(np.sum((claude == 1) | (gpt == 1)))
        report = {
            "schema_version": 1,
            "comparison_version": "vv-validation-annotator-agreement-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "labels": labels, "validation_news": len(validation), "lineage": lineage,
            "train_contents_used": False, "test_contents_used": False,
            "models_retrained": False, "new_inference_performed": False, "thresholds_changed": False,
            "source_agreement": {
                "claude_positive_pairs": int(np.sum(claude)),
                "gpt_positive_pairs": int(np.sum(gpt)),
                "consensus_positive_pairs": common_positive,
                "annotator_disagreement_pairs": int(np.sum(disagree)),
                "known_consensus_pairs": int(np.sum(known)),
                "full_label_set_agreement_articles": int(np.sum(np.all(claude == gpt, axis=1))),
                "positive_label_micro_jaccard": common_positive / union_positive if union_positive else None,
            },
            "model_provenance": provenance, "comparisons": comparisons,
            "script_sha256": sha256(Path(__file__)),
            "limitations": [
                "All results measure agreement with machine annotators; no human-reviewed ground truth.",
                "Models were trained on consensus-derived labels, so comparison against constituent annotators is not independent ground-truth validation.",
                "An omitted label in Claude/GPT is treated as zero for agreement scoring, not a verified real-world negative.",
                "Claude/GPT comparisons include all validation pairs; consensus excludes disagreement (-1). Denominators and positive support therefore differ.",
                "Macro F1 excludes zero-positive labels; macro AP requires both reference classes. Check reported label lists when comparing references.",
                "Exact-match consensus describes only fully-known articles; annotator views use all validation articles.",
                "Thresholds are the existing saved defaults, not matched-precision operating points.",
                "Rare-label estimates remain unstable; high agreement with one annotator does not prove it is more correct.",
                "Validation has been reused for exploration. Test remains reserved; no final model winner is selected.",
            ],
        }
        summary = {k: v for k, v in report.items() if k != "comparisons"}
        summary["comparisons"] = {
            key: {view: {k: v for k, v in metrics.items() if k != "per_label"} for view, metrics in results.items()}
            for key, results in comparisons.items()
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir / "comparison_report.json", report)
        write_json(args.output_dir / "comparison_summary.json", summary)
        print("\nLABEL SOURCE COMPARISON COMPLETED")
        print(f"Validation news: {len(validation)}; labels: {len(labels)}; models: {len(args.models)}")
        print(f"Claude/GPT evaluated pairs each: {claude.size}")
        print(f"Consensus evaluated pairs: {int(np.sum(known))}; unknown excluded: {int(np.sum(disagree))}")
        print("Micro F1 against each reference:")
        print(f"{'Model':<18} {'Claude':>10} {'GPT':>10} {'Consensus':>10}")
        for key, results in comparisons.items():
            values = [results[view]["micro"]["f1"] for view in views]
            formatted = [f"{value:.4f}" if value is not None else "N/A" for value in values]
            print(f"{key:<18} {formatted[0]:>10} {formatted[1]:>10} {formatted[2]:>10}")
        print("Training / new inference / threshold changes: NO")
        print("Train/test contents used: NO")
        print(f"Files saved to: {args.output_dir.resolve()}")
        print("Annotator agreement only; no human accuracy or final winner established.")
    except (FileNotFoundError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"COMPARISON FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
