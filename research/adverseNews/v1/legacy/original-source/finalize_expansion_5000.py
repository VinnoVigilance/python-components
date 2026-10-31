"""Validate GPT expansion labels and assemble the provenance-preserving 5,000 corpus."""

import argparse
from collections import Counter
import json
from pathlib import Path

from contracts import file_digest


DEFAULT_EXPANSION = Path("research/adverseNews/data/labeling/expansion-4500-v2")
DEFAULT_PILOT = Path("research/adverseNews/data/labeling/final-pilot-500-v2/training-pool-500-title-body-labels.jsonl")
DEFAULT_OUTPUT = Path("research/adverseNews/data/labeling/final-pilot-5000-v1")


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expansion", type=Path, default=DEFAULT_EXPANSION)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--labels", type=Path, default=DEFAULT_EXPANSION / "returns/gpt6-luna/labels-proposed-silver.jsonl")
    parser.add_argument("--corrections", type=Path,
                        default=DEFAULT_EXPANSION / "returns/gpt6-luna/review-corrections.jsonl")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--model", default="GPT-6 Luna / low reasoning")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("final output exists; choose a new versioned output path")

    session = json.loads((args.expansion / "session.json").read_text(encoding="utf-8"))
    if session["selected_articles"] != 4500 or session["reserved_reference_pool_used"]:
        raise ValueError("unexpected expansion manifest or reference-pool selection")
    if file_digest(args.expansion / "article-map.jsonl") != session["article_map_sha256"]:
        raise ValueError("expansion article map hash mismatch")
    if file_digest(args.expansion / "articles-title-body-only.jsonl") != session["title_body_file_sha256"]:
        raise ValueError("expansion title/body packet-source hash mismatch")

    pilot = read_jsonl(args.pilot)
    if len(pilot) != 500:
        raise ValueError("pilot input must contain exactly 500 rows")
    selected = read_jsonl(args.expansion / "selected-articles-with-metadata.jsonl")
    mapping = read_jsonl(args.expansion / "article-map.jsonl")
    labels = read_jsonl(args.labels)
    corrections = read_jsonl(args.corrections) if args.corrections.exists() else []
    requests = {x["request_article_id"]: x for x in mapping}
    articles = {x["article_id"]: x for x in selected}
    if len(requests) != 4500 or len(articles) != 4500:
        raise ValueError("expected 4,500 unique mapped articles")
    if len(labels) != 4500:
        raise ValueError("GPT labels are incomplete; no fallback labels are allowed")
    label_map = {}
    for row in labels:
        if set(row) != {"article_id", "label"}:
            raise ValueError("each GPT label row must contain only article_id and label")
        if not isinstance(row["article_id"], str) or row["article_id"] in label_map:
            raise ValueError("duplicate or invalid GPT request ID")
        if type(row["label"]) is not int or row["label"] not in (0, 1):
            raise ValueError("label must be integer 0 or 1")
        label_map[row["article_id"]] = row["label"]
    if set(label_map) != set(requests):
        missing, extra = set(requests) - set(label_map), set(label_map) - set(requests)
        raise ValueError(f"GPT IDs mismatch: missing={len(missing)}, extra={len(extra)}")
    correction_map = {}
    for row in corrections:
        if set(row) != {"request_id", "original_label", "corrected_label", "reason"}:
            raise ValueError("correction rows must contain request_id, original_label, corrected_label, and reason")
        rid = row["request_id"]
        if rid not in label_map or rid in correction_map:
            raise ValueError(f"unknown or duplicate correction ID: {rid}")
        if (type(row["original_label"]) is not int or row["original_label"] != label_map[rid]
                or type(row["corrected_label"]) is not int or row["corrected_label"] not in (0, 1)
                or row["corrected_label"] == row["original_label"] or not row["reason"].strip()):
            raise ValueError(f"invalid correction for {rid}")
        correction_map[rid] = row

    # Reattach only metadata from the immutable prepared DB snapshot to the
    # existing 500; do not alter their established labels or label provenance.
    snapshot_root = Path("research/adverseNews/data/prepared/db-selection-v3")
    prepared_by_id = {r["article_id"]: r for file in (snapshot_root / "by-dataset").glob("*.jsonl")
                      for r in read_jsonl(file)}
    splits_by_id = {r["article_id"]: r for r in read_jsonl(snapshot_root / "provisional-splits.jsonl")}
    combined = []
    for row in pilot:
        item = dict(row)
        prepared_id = item["physical_article_id"]
        source = prepared_by_id.get(prepared_id)
        split = splits_by_id.get(prepared_id)
        if source is None or split is None:
            raise ValueError(f"missing source metadata for pilot article {prepared_id}")
        item.update({"prepared_group_id": split["group_id"],
                     "language_metadata": source.get("language", ""),
                     "published_date": source.get("published_date", ""),
                     "url": source.get("url", ""),
                     "candidate_split_at_preparation": split["candidate_split"]})
        combined.append(item)

    for request_id, route in requests.items():
        article = articles[route["article_id"]]
        if route["content_hash"] != article["content_hash"]:
            raise ValueError(f"content hash mismatch for {request_id}")
        raw_label = label_map[request_id]
        correction = correction_map.get(request_id)
        label = correction["corrected_label"] if correction else raw_label
        combined.append({
            "article_id": request_id, "request_article_id": request_id,
            "physical_article_id": article["article_id"], "prepared_article_id": article["article_id"],
            "prepared_group_id": article["group_id"], "automatic_group_id": article["group_id"],
            "content_hash": article["content_hash"], "title": article["title"], "body": article["body"],
            "label": label, "gpt_label": raw_label, "deepseek_label": None,
            "label_origin": ("gpt6_luna_parent_policy_qc_corrected" if correction
                             else "gpt6_luna_proposed_silver"),
            "label_status": ("proposed_silver_corrected_after_policy_qc" if correction
                             else "proposed_silver"),
            "parent_policy_qc_correction": correction,
            "independent_blind_human_reference": False, "model_input_fields": ["title", "body"],
            "policy_version": session["policy_version"], "source_name": article["source_name"],
            "dataset_name": article["dataset_name"], "language_metadata": article.get("language", ""),
            "published_date": article.get("published_date", ""), "url": article.get("url", ""),
            "candidate_split_at_preparation": article["candidate_split"],
            "gpt_model_and_mode": args.model, "labeling_session": str(args.expansion),
            "labeling_batch_id": route["batch_id"], "full_body_supplied": True,
        })

    if len(combined) != 5000:
        raise ValueError(f"combined row count is {len(combined)}, expected 5,000")
    if len({r["content_hash"] for r in combined}) != 5000:
        raise ValueError("combined pool contains duplicate content hashes")
    if len({r["prepared_group_id"] for r in combined}) != 5000:
        raise ValueError("combined pool contains duplicate prepared groups")
    if any(type(r["label"]) is not int or r["label"] not in (0, 1) for r in combined):
        raise ValueError("combined pool contains invalid labels")

    combined.sort(key=lambda r: r["article_id"])
    args.output.mkdir(parents=True)
    out = args.output / "final-5000-title-body-labels.jsonl"
    write_jsonl(out, combined)
    source_counts = Counter(r["source_name"] for r in combined)
    dataset_counts = Counter(r["dataset_name"] for r in combined)
    label_counts = Counter(str(r["label"]) for r in combined)
    origin_counts = Counter(r["label_origin"] for r in combined)
    language_counts = Counter(r.get("language_metadata") or "<unknown>" for r in combined)
    manifest = {
        "schema_version": "adverse-news-final-label-pool-v1", "status": "complete_label_pool_not_independent_benchmark",
        "articles": len(combined), "positive": label_counts["1"], "negative": label_counts["0"],
        "label_origin_counts": dict(origin_counts), "source_counts": dict(sorted(source_counts.items())),
        "dataset_counts": dict(sorted(dataset_counts.items())),
        "language_metadata_counts": dict(sorted(language_counts.items())),
        "gpt_expansion_model_and_mode": args.model,
        "gpt_labels_sha256": file_digest(args.labels),
        "policy_corrections_sha256": file_digest(args.corrections) if args.corrections.exists() else None,
        "policy_correction_count": len(correction_map), "pilot_sha256": file_digest(args.pilot),
        "expansion_session_sha256": file_digest(args.expansion / "session.json"),
        "dataset_sha256": file_digest(out), "all_expansion_labels_validated": True,
        "independent_human_reference_count": sum(bool(r.get("independent_blind_human_reference")) for r in combined),
        "teacher_labels_are_not_truth": True,
        "reserved_reference_pool_used_for_labeling": False,
        "same_case_review_complete": False,
        "model_inputs": ["title", "body"],
        "candidate_splits_are_provisional": True,
        "note": "This is a complete 5,000-row proposed-label corpus, not a production benchmark. Rebuild case-safe evaluation splits after duplicate/case review and create an independent answer-blind human reference.",
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "articles": len(combined),
                      "labels": {"1": label_counts["1"], "0": label_counts["0"]},
                      "label_origins": dict(origin_counts), "sources": dict(source_counts),
                      "output": str(out)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
