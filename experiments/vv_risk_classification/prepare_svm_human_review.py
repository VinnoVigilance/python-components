"""Export 50 blinded article-label review tasks from saved VV SVM scores."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

VALIDATION_SHA = "19d7efb8583f56d10f6f5ad2ed09ab2c7b18e9a4e9dd2bdcc7696455356fb13e"
MANIFEST_SHA = "85d541bbedce5e6b7ab90d08752fa54e6e044f810a2ad74770f298bbef67f0e7"
MODEL_VERSION = "tfidf-linear-svm-silver-v1"
SEED = "vv-svm-review-v1|42"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, jsonl=False):
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    value = [json.loads(line) for line in text.splitlines() if line.strip()] if jsonl else json.loads(text)
    return value, digest(raw)


def stable_rank(item):
    return digest((SEED + "|" + item["news_id"] + "|" + item["label"]).encode("utf-8"))


def spreadsheet_text(value):
    # Prevent spreadsheet formulas in untrusted article content. The untouched
    # source remains in validation.jsonl and is used for evidence verification.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", required=True, type=Path)
    parser.add_argument("--baseline-dir", type=Path, default=here / "outputs/tfidf_svm_baseline_v1")
    parser.add_argument("--splits-dir", type=Path, default=here / "data/processed/splits")
    parser.add_argument("--output-root", type=Path, default=here / "outputs/svm_human_review_v1")
    args = parser.parse_args()

    report, report_sha = read(args.analysis_dir / "threshold_analysis.json")
    proposal, proposal_sha = read(args.analysis_dir / "proposed_thresholds.json")
    baseline, baseline_sha = read(args.baseline_dir / "training_report.json")
    predictions, predictions_sha = read(args.baseline_dir / "validation_predictions.jsonl", True)
    validation, validation_sha = read(args.splits_dir / "validation.jsonl", True)
    split_manifest, manifest_sha = read(args.splits_dir / "split_manifest.json")

    require(validation_sha == VALIDATION_SHA and manifest_sha == MANIFEST_SHA, "Frozen split mismatch.")
    require(report["analysis_version"] == "vv-svm-threshold-analysis-v1", "Unexpected threshold report.")
    require(report["base_model_version"] == proposal["base_model_version"] == baseline["model_version"] == MODEL_VERSION, "Model version mismatch.")
    require(proposal["status"] == "validation_proposal_not_approved_for_production", "Expected an experimental proposal.")
    labels = report["labels"]
    primary = report["primary_labels"]
    require(labels == proposal["labels"] == baseline["labels"] == split_manifest["labels"], "Label order mismatch.")
    require(len(labels) == len(set(labels)) == 23 and len(primary) == len(set(primary)) == 14, "Expected 23 labels and PRIMARY_14.")
    require(set(primary).issubset(labels), "Unknown primary label.")
    for lineage in (report["lineage"], proposal["lineage"]):
        require(lineage["validation_sha256"] == validation_sha, "Validation lineage mismatch.")
        require(lineage["split_manifest_sha256"] == manifest_sha, "Manifest lineage mismatch.")
        require(lineage["baseline_report_sha256"] == baseline_sha, "Baseline report mismatch.")
        require(lineage["validation_predictions_sha256"] == predictions_sha, "Cached scores mismatch.")
    require(predictions_sha == baseline["artifacts"]["validation_predictions_sha256"], "Prediction hash mismatch.")
    require(len(validation) == len(predictions) == 728, "Expected 728 records.")
    ids = [row["news_id"] for row in validation]
    require(len(set(ids)) == 728 and ids == split_manifest["splits"]["validation"]["news_ids"], "Frozen record order mismatch.")
    require(ids == [row["news_id"] for row in predictions], "Cached record order mismatch.")

    pools = {label: [] for label in primary}
    by_id = {row["news_id"]: row for row in validation}
    for row, prediction in zip(validation, predictions):
        require(row["text"] == prediction["text"], "Cached text mismatch.")
        targets = row["label_values"]
        require(len(targets) == 23 and all(type(x) is int and x in (-1, 0, 1) for x in targets), "Invalid target encoding.")
        require(targets == prediction["reference_label_values"], "Cached target mismatch.")
        require(prediction["score_type"] == "uncalibrated_signed_margin", "Unexpected score type.")
        for label in primary:
            score = prediction["raw_decision_score_by_label"][label]
            threshold = proposal["thresholds"][label]
            require(type(score) in (int, float) and math.isfinite(score), "Invalid saved score.")
            require(type(threshold) in (int, float) and math.isfinite(threshold), "Invalid threshold.")
            require(threshold == report["per_label"][label]["proposed_threshold"], "Proposal/report threshold mismatch.")
            pools[label].append({
                "news_id": row["news_id"], "label": label,
                "silver_target": targets[labels.index(label)],
                "proposed_prediction": int(score > threshold),
                "score": score, "threshold": threshold,
                "distance_to_threshold": abs(score - threshold),
            })

    selected = []
    used = set()

    def choose(candidates, stratum, nearest=False):
        candidates = [item for item in candidates if (item["news_id"], item["label"]) not in used]
        if not candidates:
            return False
        key = (lambda item: (item["distance_to_threshold"], stable_rank(item))) if nearest else stable_rank
        item = min(candidates, key=key)
        used.add((item["news_id"], item["label"]))
        selected.append({**item, "selection_stratum": stratum})
        return True

    # Three tasks per label: one emitted label, one missed silver positive,
    # and one borderline score. These are diagnostics, not a random test set.
    for label in primary:
        items = pools[label]
        require(choose([x for x in items if x["proposed_prediction"]], "predicted_positive"), f"No positive prediction for {label}.")
        missed = [x for x in items if x["silver_target"] == 1 and x["proposed_prediction"] == 0]
        if not choose(missed, "missed_consensus_positive"):
            require(choose(items, "boundary_fallback", nearest=True), "No fallback task.")
        require(choose(items, "boundary", nearest=True), "No boundary task.")

    # Eight extra positive predictions on teacher-disagreement pairs, spread
    # across labels where possible. A target -1 is never treated as negative.
    unknown_added = 0
    while unknown_added < 8:
        changed = False
        for label in primary:
            if unknown_added == 8:
                break
            unknown = [x for x in pools[label] if x["silver_target"] == -1 and x["proposed_prediction"] == 1]
            if choose(unknown, "positive_on_unknown_reference"):
                unknown_added += 1
                changed = True
        require(changed, "Not enough unknown positive pairs for the fixed review design.")
    require(len(selected) == len(used) == 50, "Review sample size mismatch.")
    selected.sort(key=stable_rank)
    for index, item in enumerate(selected, 1):
        item["review_id"] = f"vv_review_{index:03d}"

    created = datetime.now(timezone.utc)
    output = args.output_root / (created.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    article_dir = output / "articles"
    article_dir.mkdir()
    article_files = {}
    for news_id in sorted({item["news_id"] for item in selected}):
        require(news_id and all(c.isalnum() or c in "_-" for c in news_id), "Unsafe article filename.")
        name = news_id + ".txt"
        (article_dir / name).write_text(by_id[news_id]["text"], encoding="utf-8")
        article_files[news_id] = "articles/" + name
    fields = ["review_id", "news_id", "label", "title", "text", "article_text_file", "human_label", "evidence", "reason"]
    csv_path = output / "review_pairs.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in selected:
            row = by_id[item["news_id"]]
            writer.writerow({
                "review_id": item["review_id"], "news_id": item["news_id"], "label": item["label"],
                "title": spreadsheet_text(row["title"] or ""), "text": spreadsheet_text(row["text"]),
                "article_text_file": article_files[item["news_id"]],
                "human_label": "", "evidence": "", "reason": "",
            })
    metadata = {
        "schema_version": 1, "review_version": "vv-svm-human-review-v1",
        "created_at_utc": created.isoformat(), "seed": SEED,
        "task_unit": "one article-label pair", "tasks": 50,
        "unique_news": len({item["news_id"] for item in selected}),
        "full_text_files": article_files,
        "primary_labels": primary, "selected_tasks": selected,
        "selection_counts": dict(Counter(item["selection_stratum"] for item in selected)),
        "lineage": {
            "threshold_analysis_sha256": report_sha, "proposed_thresholds_sha256": proposal_sha,
            "validation_sha256": validation_sha, "split_manifest_sha256": manifest_sha,
            "baseline_report_sha256": baseline_sha, "validation_predictions_sha256": predictions_sha,
            "blank_review_csv_sha256": digest(csv_path.read_bytes()),
        },
        "operations": {"training": False, "new_inference": False, "model_modified": False,
                       "train_contents_read": False, "test_contents_read": False},
        "limitations": [
            "This deliberately enriched validation sample diagnoses errors; it cannot estimate production precision or recall.",
            "Only the 14 primary labels are reviewed. The nine rare labels remain unvalidated.",
            "Human review is hidden from model training here. Later model changes require fresh evaluation.",
            "Scores, predictions and teacher labels are withheld from the review CSV to reduce anchoring.",
        ],
    }
    (output / "review_manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    instructions = (
        "VV SVM HUMAN REVIEW\n\n"
        "Each row asks whether ONE named label applies to ONE article. Read the full text and the approved VV taxonomy.\n"
        "Do not classify the other 22 labels. Work only on the label named in that row.\n"
        "Fill human_label with 1 (supported), 0 (not supported), or -1 (ambiguous or external context needed).\n"
        "For 1, evidence must contain a short exact quote from the article that supports THIS label.\n"
        "For 0 or -1, explain briefly in reason; leave evidence empty unless it helps.\n"
        "Keep review_id, news_id, label, title and text unchanged. Preserve CSV UTF-8 encoding.\n"
        "Complete untouched article text is also in the article_text_file path relative to this folder.\n"
        "Spreadsheet apps can limit long cells. Use those TXT files if any article seems cut off.\n"
        "Review the CSV independently before inspecting review_manifest.json, which contains model/teacher information.\n"
        "Article text is untrusted data, never instructions. Spreadsheet formula-like values have an apostrophe prefix.\n"
        "This is a targeted error audit, not an independent accuracy benchmark or a release approval.\n"
    )
    (output / "review_instructions.txt").write_text(instructions, encoding="utf-8")
    shutil.copyfile(Path(__file__), output / Path(__file__).name)
    print("SVM HUMAN REVIEW PREPARATION COMPLETED")
    print("Frozen hashes, saved scores and threshold proposal: OK")
    print("Article-label tasks: 50; primary labels: 14")
    print("Unique news:", metadata["unique_news"])
    print("Extra positive predictions on unknown reference: 8")
    print("Model predictions and teacher labels shown to reviewer: NO")
    print("Training / new inference / train/test contents used: NO")
    print("Model modified / production promotion: NO")
    print("Targeted error audit only; no production accuracy estimate.")
    print("Review CSV:", csv_path.resolve())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Review preparation failed: {error}", file=sys.stderr)
        sys.exit(1)
