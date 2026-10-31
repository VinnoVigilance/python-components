"""Join the completed 100+400 article pilot labels to title/body text."""

import argparse
from collections import Counter
import json
from pathlib import Path

from contracts import file_digest


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path, records):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def join_source(session, labels, id_key):
    mapping = {r["request_article_id"]: r for r in rows(session / "article-map.jsonl")}
    text = {r["article_id"]: r for r in rows(session / "articles-title-body-only.jsonl")}
    joined = []
    for label in labels:
        rid = label[id_key]
        route = mapping[rid]
        article = text[rid]
        if label["content_hash"] != route["content_hash"]:
            raise ValueError(f"content hash mismatch for {rid}")
        if label["article_id"] not in (route["article_id"], rid):
            raise ValueError(f"physical article ID mismatch for {rid}")
        joined.append({**article, "request_article_id": rid,
                       "physical_article_id": route["article_id"],
                       "automatic_group_id": route["group_id"],
                       "source_name": route["source_name"],
                       "dataset_name": route["dataset_name"],
                       "candidate_split": route["candidate_split"],
                       "content_hash": label["content_hash"], "label": label["label"],
                       "label_origin": label.get("origin", label.get("review_status")),
                       "label_status": label.get("status", label.get("review_status")),
                       "deepseek_label": label.get("deepseek_label", label.get("deepseek_original_label")),
                       "gpt_label": label.get("gpt_label", label.get("gpt_original_label")),
                       "policy_version": label["policy_version"] if "policy_version" in label else "adverse-news-policy-v0.2",
                       "independent_blind_human_reference": label.get("independent_blind_human_reference", False),
                       "model_input_fields": ["title", "body"]})
    return joined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--expansion", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("choose a new output directory")
    pilot_labels = rows(args.pilot / "comparison-final" / "pilot-labels-with-review-status.jsonl")
    expansion_labels = rows(args.expansion / "comparison" / "final-labels-400.jsonl")
    if len(pilot_labels) != 100 or len(expansion_labels) != 400:
        raise ValueError("expected completed 100-article pilot and 400-article expansion")
    combined = join_source(args.pilot, pilot_labels, "request_article_id") + join_source(args.expansion, expansion_labels, "request_article_id")
    ids = [row["request_article_id"] for row in combined]
    physical_ids = [row["physical_article_id"] for row in combined]
    if len(set(ids)) != 500 or len(set(physical_ids)) != 500:
        raise ValueError("duplicate request or physical article IDs across combined pool")
    if any(type(row["label"]) is not int or row["label"] not in (0, 1) for row in combined):
        raise ValueError("every final article must have an integer 0/1 target")
    args.output.mkdir(parents=True)
    output = args.output / "training-pool-500-title-body-labels.jsonl"
    write_rows(output, combined)
    manifest = {
        "schema_version": "adverse-news-training-pool-500-v1", "articles": len(combined),
        "label_counts": dict(Counter(str(row["label"]) for row in combined)),
        "origin_counts": dict(Counter(row["label_origin"] for row in combined)),
        "status_counts": dict(Counter(row["label_status"] for row in combined)),
        "model_input_fields": ["title", "body"], "metadata_and_label_fields_are_not_model_features": True,
        "independent_human_reference_available": False, "training_started": False,
        "pilot_labels_sha256": file_digest(args.pilot / "comparison-final" / "pilot-labels-with-review-status.jsonl"),
        "expansion_labels_sha256": file_digest(args.expansion / "comparison" / "final-labels-400.jsonl"),
        "pilot_map_sha256": file_digest(args.pilot / "article-map.jsonl"),
        "expansion_map_sha256": file_digest(args.expansion / "article-map.jsonl"),
        "output_sha256": file_digest(output),
        "limitations": ["Teacher agreement is not accuracy.",
                        "User adjudications followed discussion or disagreement-only review and are not an independent production benchmark.",
                        "The pool is not representative of production prevalence.",
                        "Run initial experiments only after explicit training approval; evaluate production readiness on separate human reference data."],
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"articles": len(combined), "label_counts": manifest["label_counts"],
                      "origin_counts": manifest["origin_counts"], "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
