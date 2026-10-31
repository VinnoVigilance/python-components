"""Apply the user's answer-hidden decisions to the paired expansion labels."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from contracts import file_digest


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path, rows):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    decision_file = args.review / "user-decisions-2026-10-28.txt"
    key_path = args.review / "selection-key-DO-NOT-READ-BEFORE-REVIEW.jsonl"
    review_path = args.review / "human-review-disagreements.md"
    review_manifest_path = args.review / "manifest.json"
    comparison_path = args.session / "comparison"
    key = read_rows(key_path)
    manifest = json.loads(review_manifest_path.read_text(encoding="utf-8"))
    if file_digest(review_path) != manifest["review_file_sha256"]:
        raise ValueError("answer-hidden review file changed after preparation")

    text = decision_file.read_text(encoding="utf-8")
    decisions = {}
    for match in re.finditer(r"DIS-(\d{2})\s*=\s*([01])\s*\r?\nReason:\s*([\s\S]*?)(?=\r?\n\r?\nDIS-\d{2}\s*=|$)", text):
        review_id = f"DIS-{match.group(1)}"
        if review_id in decisions:
            raise ValueError(f"duplicate decision {review_id}")
        decisions[review_id] = {"label": int(match.group(2)), "rationale": match.group(3).strip()}
    expected_ids = {row["review_id"] for row in key}
    if set(decisions) != expected_ids:
        raise ValueError(f"decisions incomplete or extra: got {len(decisions)}, expected {len(expected_ids)}")
    if any(not row["rationale"] for row in decisions.values()):
        raise ValueError("each decision needs its stated rationale")

    if (args.review / "human-disagreement-adjudications.jsonl").exists():
        raise FileExistsError("adjudication artifact already exists")
    response_hash = file_digest(decision_file)
    adjudications = []
    decisions_by_rid = {}
    for row in key:
        decision = decisions[row["review_id"]]
        updated = {**row, "human_label": decision["label"], "status": "human_adjudicated_after_answer_hidden_disagreement_review"}
        adjudications.append({
            "adjudication_id": hashlib.sha256((response_hash + row["review_id"]).encode()).hexdigest(),
            "review_id": row["review_id"], "article_id": row["article_id"],
            "request_article_id": row["request_article_id"], "content_hash": row["content_hash"],
            "label": decision["label"], "rationale": decision["rationale"],
            "origin": "user_adjudication_after_answer_hidden_review", "reviewed_by": "user",
            "review_file_sha256": manifest["review_file_sha256"],
            "user_response_sha256": response_hash, "deepseek_label": row["deepseek_label"],
            "gpt_label": row["gpt_label"], "model_outputs_hidden_during_review": True,
            "selection_condition": "selected because the two teacher labels disagreed",
            "independent_production_reference": False,
        })
        decisions_by_rid[row["request_article_id"]] = {**decision, "review_id": row["review_id"]}

    write_rows(args.review / "human-disagreement-adjudications.jsonl", adjudications)
    resolved_key_path = args.review / "selection-key-resolved.jsonl"
    write_rows(resolved_key_path, [{**row, "human_label": decisions[row["review_id"]]["label"],
                                    "status": "human_adjudicated"} for row in key])
    manifest.update({"human_labels_received": len(decisions), "review_status": "complete",
                     "user_response_sha256": response_hash,
                     "adjudications_sha256": file_digest(args.review / "human-disagreement-adjudications.jsonl"),
                     "resolved_key_sha256": file_digest(resolved_key_path),
                     "independent_production_reference": False})
    review_manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    agreements = read_rows(comparison_path / "agreement-labels-proposed-silver.jsonl")
    disagreements = read_rows(comparison_path / "disagreements-pending-review.jsonl")
    finals = []
    for row in agreements:
        finals.append({"article_id": row["article_id"], "request_article_id": row["request_article_id"],
                       "content_hash": row["content_hash"], "label": row["label"],
                       "deepseek_label": row["deepseek_label"], "gpt_label": row["gpt_label"],
                       "origin": "two_model_agreement", "status": "proposed_silver",
                       "human_reference": False})
    for row in disagreements:
        decision = decisions_by_rid[row["request_article_id"]]
        finals.append({"article_id": row["article_id"], "request_article_id": row["request_article_id"],
                       "content_hash": row["content_hash"], "label": decision["label"],
                       "deepseek_label": row["deepseek_label"], "gpt_label": row["gpt_label"],
                       "origin": "user_adjudication_after_answer_hidden_disagreement_review",
                       "status": "human_adjudicated", "human_reference": False,
                       "adjudication_review_id": decision["review_id"]})
    if len(finals) != 400 or len({r["request_article_id"] for r in finals}) != 400:
        raise ValueError("final label set must contain exactly 400 unique articles")
    write_rows(comparison_path / "final-labels-400.jsonl", finals)

    labels = {row["request_article_id"]: row["label"] for row in finals}
    model_rows = {row["request_article_id"]: row for row in disagreements}
    ds_matches = sum(decisions_by_rid[rid]["label"] == row["deepseek_label"] for rid, row in model_rows.items())
    gpt_matches = sum(decisions_by_rid[rid]["label"] == row["gpt_label"] for rid, row in model_rows.items())
    old_summary_path = comparison_path / "summary.json"
    summary = json.loads(old_summary_path.read_text(encoding="utf-8"))
    from collections import Counter
    summary.update({"user_adjudicated_disagreements": len(decisions),
                    "disagreements_remaining": 0,
                    "final_label_counts_after_adjudication": dict(Counter(str(v) for v in labels.values())),
                    "user_decisions_match_deepseek_on_disagreements": ds_matches,
                    "user_decisions_match_gpt_on_disagreements": gpt_matches,
                    "model_match_counts_are_disagreement_only_not_accuracy_estimates": True,
                    "human_reference_created": False,
                    "final_labels_sha256": file_digest(comparison_path / "final-labels-400.jsonl"),
                    "user_adjudications_sha256": file_digest(args.review / "human-disagreement-adjudications.jsonl")})
    summary_lines = ["# Paired expansion: label comparison and adjudication", "",
                     "- Paired articles: 400", "- Raw-model agreement: 382/400 (95.5%); this is not accuracy.",
                     "- Initial disagreements: 18; all 18 were adjudicated by the user after answer-hidden review.",
                     f"- User decisions aligned with DeepSeek on {ds_matches}/18 and GPT-6 Luna on {gpt_matches}/18 of these selected disagreements only.",
                     "- These conditional match counts are not model-accuracy estimates.",
                     f"- Final training-target counts: {summary['final_label_counts_after_adjudication']}.",
                     "- Agreed cases remain proposed silver; disputed cases carry user adjudications.",
                     "- The 400 articles are training candidates, not production-prevalence or held-out reference data.",
                     "- DeepSeek model/mode was not supplied. GPT used GPT-6 Luna with low reasoning.",
                     "- No training or CV has started. A separate answer-blind human reference is still required for production evaluation.",
                     "", "See `final-labels-400.jsonl`, `agreement-labels-proposed-silver.jsonl`, `disagreements-pending-review.jsonl`, and `../human-adjudication-v1/human-disagreement-adjudications.jsonl`.", ""]
    (comparison_path / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")
    summary["outputs_sha256"] = {name: file_digest(comparison_path / name) for name in
                                  ("agreement-labels-proposed-silver.jsonl", "disagreements-pending-review.jsonl",
                                   "final-labels-400.jsonl", "summary.md")}
    old_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total_final_labels": len(finals), "label_counts": summary["final_label_counts_after_adjudication"],
                      "disagreements_remaining": 0, "matched_deepseek_on_selected_disagreements": ds_matches,
                      "matched_gpt_on_selected_disagreements": gpt_matches}, indent=2))


if __name__ == "__main__":
    main()
