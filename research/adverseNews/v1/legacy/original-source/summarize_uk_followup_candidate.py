"""Rebuild a comparison summary from the already-fitted candidate scores."""

import csv
import json
from collections import defaultdict

from contracts import file_digest
from fit_uk_followup_candidate import ROOT, OUT, FOLLOW, metrics


def main():
    if (OUT / "manifest.json").exists():
        raise ValueError("Candidate manifest already exists; do not overwrite frozen results")
    with (OUT / "comparison.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["packet"]].append(row)
    summary = {}
    for packet, items in grouped.items():
        opinions = [dict(label=int(r["gpt_label"]), uncertain=r["gpt_uncertain"] == "True") for r in items]
        summary[packet] = {}
        for name in ("original", "previous_experiment", "new_candidate"):
            summary[packet][name] = metrics(opinions, [float(r[name + "_score"]) for r in items])
    manifest = dict(status="research_candidate_unpromoted", model="lsa200_rbf_c3", threshold=0.0,
                    sklearn_version="1.9.1", original_rows=5000, policy_corrections=6,
                    earlier_certain_additions=18, new_certain_additions=26,
                    new_uncertain_excluded=10, total_training_rows=5044,
                    training_packet_sha256=file_digest(FOLLOW / "training-blind.jsonl"),
                    training_review_sha256=file_digest(FOLLOW / "training-gpt-review.jsonl"),
                    evaluation_packet_sha256=file_digest(FOLLOW / "evaluation-blind.jsonl"),
                    evaluation_review_sha256=file_digest(FOLLOW / "evaluation-gpt-review.jsonl"),
                    candidate_bundle_sha256=file_digest(OUT / "candidate-bundle.joblib"),
                    comparison_sha256=file_digest(OUT / "comparison.csv"),
                    independent_human_reference=False, scores=summary)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
