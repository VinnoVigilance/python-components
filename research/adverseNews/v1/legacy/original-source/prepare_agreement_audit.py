"""Select a small answer-hidden review of model agreements; no labeling/models."""

import argparse
from collections import Counter
import json
from pathlib import Path

from contracts import digest, file_digest


SEED = "pdg115-agreement-audit-v1"


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("audit directory exists; refusing overwrite")
    label_path = args.pilot / "comparison-final/pilot-labels-with-review-status.jsonl"
    article_path = args.pilot / "articles-title-body-only.jsonl"
    map_path = args.pilot / "article-map.jsonl"
    labels = read_rows(label_path)
    articles = {r["article_id"]: r for r in read_rows(article_path)}
    mapping = {r["request_article_id"]: r for r in read_rows(map_path)}
    pools = {}
    for target in (0, 1):
        candidates = [r for r in labels if r["review_status"] == "proposed_silver_model_agreement"
                      and r["gpt_original_label"] == target and r["deepseek_original_label"] == target]
        pools[target] = sorted(candidates, key=lambda r: digest([SEED, r["request_article_id"]]))
    chosen, used_sources, used_groups = [], set(), set()
    for target in (1, 0, 1, 0, 1, 0):
        options = [r for r in pools[target]
                   if mapping[r["request_article_id"]]["source_name"] not in used_sources
                   and mapping[r["request_article_id"]]["group_id"] not in used_groups]
        if not options:
            raise ValueError("cannot select six different publishers/groups with requested coverage")
        row = options[0]
        rid = row["request_article_id"]
        article = articles[rid]
        if digest([article["title"], article["body"]]) != row["content_hash"]:
            raise ValueError("article text hash mismatch")
        if mapping[rid]["candidate_split"] != "train_candidate":
            raise ValueError("audit must not consume reference-reserved articles")
        chosen.append(row)
        used_sources.add(mapping[rid]["source_name"])
        used_groups.add(mapping[rid]["group_id"])
    # Presentation order is independent of class and original request order.
    chosen.sort(key=lambda r: digest([SEED, "presentation", r["request_article_id"]]))
    args.output.mkdir(parents=True, exist_ok=False)
    key = []
    review_items = []
    for index, row in enumerate(chosen, 1):
        rid = row["request_article_id"]
        item = {"audit_id": f"AUDIT-{index:02d}", **articles[rid]}
        review_items.append(item)
        key.append({"audit_id": item["audit_id"], "request_article_id": rid,
                    "article_id": row["article_id"], "content_hash": row["content_hash"],
                    "model_label": row["label"], "source_name": mapping[rid]["source_name"],
                    "group_id": mapping[rid]["group_id"], "human_review_status": "not_received"})
    with (args.output / "selection-key-DO-NOT-READ-BEFORE-REVIEW.jsonl").open("x", encoding="utf-8") as stream:
        for row in key:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    intro = """# Small agreement audit

These are previously presented pilot articles, with model answers and original
IDs hidden. They are not fresh held-out evaluation articles. Read each full
title/body and apply the agreed PDG-115 policy independently. Any substantive
qualifying matter can make the article 1 after the resolution-only exception.
Victims/witnesses do not inherit guilt. Routine information, criticism and costs
alone are not enough. No external search or risk-category output is needed.

Assign integer 0 or 1 and give a brief reason or exact supporting passage.
Do not try to match a presumed balance or either model. If extraction prevents
reading, report the input problem separately rather than inventing label 0.

Reply in this chat, for example: AUDIT-01 = YOUR_INTEGER_LABEL; reason/passage.
The empty fields below are workflow placeholders, not labels. Model agreement
does not establish truth; your decision can differ. Do not open the selection-key
file before reviewing. Start with the first three; the next three can follow.
"""
    for start, filename in ((0, "audit-first-3.md"), (3, "audit-next-3.md")):
        sections = [intro]
        for article in review_items[start:start + 3]:
            sections.append(f"\n## {article['audit_id']}\n\nTitle: {article['title']}\n\n"
                            f"{article['body']}\n\nYour label (0 or 1):\nReason / exact passage:\n")
        (args.output / filename).write_text("\n".join(sections), encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": "adverse-agreement-audit-v1", "seed": SEED,
        "builder_sha256": file_digest(Path(__file__)),
        "input_labels_sha256": file_digest(label_path), "article_map_sha256": file_digest(map_path),
        "input_articles_sha256": file_digest(article_path), "articles": len(chosen),
        "different_publishers": len(used_sources), "different_automatic_groups": len(used_groups),
        "selection": "Seeded greedy selection, three model-positive and three model-negative agreements, different publishers/groups; previously human-confirmed examples excluded",
        "model_label_counts_in_selection": dict(Counter(r["label"] for r in chosen)),
        "length_filter": "none; full original bodies retained", "labels_hidden_in_review_files": True,
        "prior_exposure_possible": True, "not_independent_final_reference": True,
        "human_labels_received": 0, "model_calls_made": 0, "training_performed": False,
        "limitations": "Small exploratory, enriched review; cannot estimate production accuracy or prevalence",
        "output_sha256": {p.name: file_digest(p) for p in sorted(args.output.iterdir()) if p.is_file()},
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"articles": len(chosen), "publishers": len(used_sources),
                      "full_body_characters": [len(r["body"]) for r in review_items],
                      "review_files": ["audit-first-3.md", "audit-next-3.md"],
                      "human_labels_received": 0}, indent=2))


if __name__ == "__main__":
    main()
