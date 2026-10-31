"""Compare two strictly imported label sets without treating agreement as truth."""

import argparse
from collections import Counter
import json
from pathlib import Path

from contracts import file_digest


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.session / "session.json").read_text(encoding="utf-8"))
    mapping = {r["request_article_id"]: r for r in read_rows(args.session / "article-map.jsonl")}
    articles = {r["article_id"]: r for r in read_rows(args.session / "articles-title-body-only.jsonl")}
    deepseek, gpt = {}, {}
    for provider, target in (("deepseek", deepseek), ("chatgpt", gpt)):
        for path in sorted((args.session / "annotations" / provider).glob("batch-*.jsonl")):
            for row in read_rows(path):
                rid = row["request_article_id"]
                if rid in target or rid not in mapping or row["content_hash"] != mapping[rid]["content_hash"]:
                    raise ValueError(f"duplicate, unknown, or mismatched {provider} annotation")
                if type(row["label"]) is not int or row["label"] not in (0, 1):
                    raise ValueError(f"invalid {provider} label")
                target[rid] = row
    if set(deepseek) != set(mapping) or set(gpt) != set(mapping):
        raise ValueError(f"incomplete paired sets: mapping={len(mapping)}, DeepSeek={len(deepseek)}, GPT={len(gpt)}")

    agreements, disagreements = [], []
    cross = Counter()
    for rid, item in mapping.items():
        d, g = deepseek[rid], gpt[rid]
        key = f"deepseek_{d['label']}_gpt_{g['label']}"
        cross[key] += 1
        record = {**item, "deepseek_label": d["label"], "gpt_label": g["label"],
                  "agreement": d["label"] == g["label"],
                  "deepseek_annotation_id": d["annotation_id"], "gpt_annotation_id": g["annotation_id"]}
        if d["label"] == g["label"]:
            agreements.append({**record, "label": d["label"], "origin": "two_model_agreement",
                               "status": "proposed_silver", "human_reference": False})
        else:
            disagreements.append(record)

    # Resolve physical article records explicitly through request IDs.
    disagreements = [{**row, **articles[row["request_article_id"]],
                      "human_label": None, "human_review_status": "not_received"}
                     for row in disagreements]
    output = args.session / "comparison"
    output.mkdir(exist_ok=False)
    write_rows(output / "agreement-labels-proposed-silver.jsonl", agreements)
    write_rows(output / "disagreements-pending-review.jsonl", disagreements)
    summary = {
        "schema_version": "paired-expansion-comparison-v1", "articles": len(mapping),
        "agreement_count": len(agreements), "agreement_rate": len(agreements) / len(mapping),
        "disagreement_count": len(disagreements), "labels_by_model": {
            "deepseek": dict(Counter(str(row["label"]) for row in deepseek.values())),
            "chatgpt_gpt_6_luna": dict(Counter(str(row["label"]) for row in gpt.values()))},
        "pair_crosstab": dict(cross), "agreement_is_accuracy": False,
        "agreement_source_counts": dict(Counter(row["source_name"] for row in agreements)),
        "disagreement_source_counts": dict(Counter(row["source_name"] for row in disagreements)),
        "selection_not_representative_of_production_prevalence": True,
        "human_reference_created": False, "training_started": False,
        "deepseek_reported_model_and_mode": "not supplied by user",
        "gpt_reported_model_and_mode": "GPT-6 Luna; low reasoning",
        "deepseek_aggregate_response_sha256": manifest.get("deepseek_aggregate_response_sha256"),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Paired expansion: label comparison", "",
             f"- Paired articles: {len(mapping)}", f"- Agreement: {len(agreements)} ({summary['agreement_rate']:.1%})",
             f"- Disagreement: {len(disagreements)}", "- Agreement is not accuracy; both sources are model labels.",
             "- DeepSeek model/mode was not supplied. GPT used GPT-6 Luna with low reasoning.",
             "- The sample is training-candidate data and not representative prevalence or a production benchmark.",
             "- Agreed labels are proposed silver. Disagreements remain without a target label until reviewed.",
             "", "Disputed full articles are in `disagreements-pending-review.jsonl`; proposed labels for agreements are in `agreement-labels-proposed-silver.jsonl`.",
             ""]
    (output / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    summary["outputs_sha256"] = {name: file_digest(output / name) for name in
                                  ("agreement-labels-proposed-silver.jsonl", "disagreements-pending-review.jsonl", "summary.md")}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"articles": len(mapping), "agreement_count": len(agreements),
                      "disagreement_count": len(disagreements), "pair_crosstab": dict(cross)}, indent=2))


if __name__ == "__main__":
    main()
