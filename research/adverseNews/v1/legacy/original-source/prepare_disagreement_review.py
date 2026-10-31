"""Prepare answer-hidden human adjudication of paired-label disagreements."""

import argparse
import hashlib
import json
from pathlib import Path

from contracts import file_digest


SEED = "pdg115-expansion-disagreement-review-v1"


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("choose a new review directory")
    source = args.session / "comparison" / "disagreements-pending-review.jsonl"
    rows = read_rows(source)
    if not rows:
        raise ValueError("no disagreements to review")
    rows.sort(key=lambda row: hashlib.sha256((SEED + row["request_article_id"]).encode()).hexdigest())
    args.output.mkdir(parents=True)
    review, key = [], []
    sections = [
        "# Human review: paired-model disagreements", "",
        f"There are {len(rows)} cases. These are disagreement adjudications, not a representative accuracy sample. "
        "Review each complete article using the frozen binary policy. The model labels are hidden. "
        "Use only the article's title and body for the decision. Assign integer 0 or 1, then briefly note "
        "the qualifying passage or the rule that led to 0. If article text is unusable, report that separately; "
        "do not default to 0.", "",
        "Reply with `DIS-01 = 0/1` through the final ID and a short reason or exact passage. "
        "These judgments will adjudicate training labels only; they are not an independent production benchmark.", "",
    ]
    for i, row in enumerate(rows, 1):
        opaque = f"DIS-{i:02d}"
        key.append({"review_id": opaque, "request_article_id": row["request_article_id"],
                    "article_id": row["article_id"], "content_hash": row["content_hash"],
                    "deepseek_label": row["deepseek_label"], "gpt_label": row["gpt_label"],
                    "source_name": row["source_name"], "dataset_name": row["dataset_name"],
                    "human_label": None, "status": "awaiting_human_adjudication"})
        review.append({"review_id": opaque, "request_article_id": row["request_article_id"],
                       "content_hash": row["content_hash"], "title": row["title"], "body": row["body"]})
        sections.extend([f"## {opaque}", "", f"**Title:** {row['title']}", "", row["body"],
                         "", "**Your label (0 or 1):**", "", "**Reason / exact passage:**", "", "---", ""])
    review_path = args.output / "human-review-disagreements.md"
    key_path = args.output / "selection-key-DO-NOT-READ-BEFORE-REVIEW.jsonl"
    review_path.write_text("\n".join(sections), encoding="utf-8", newline="\n")
    with key_path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in key:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = {"schema_version": "paired-label-disagreement-human-review-v1", "seed": SEED,
                "source_comparison_sha256": file_digest(source), "items": len(rows),
                "model_labels_hidden": True, "review_is_disagreement_only_not_representative": True,
                "independent_production_reference": False, "human_labels_received": 0,
                "review_file_sha256": file_digest(review_path), "answer_key_sha256": file_digest(key_path)}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"review_items": len(rows), "review_file": str(review_path),
                      "answer_key_hidden": True}, indent=2))


if __name__ == "__main__":
    main()
