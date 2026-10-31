"""Compare two frozen classifiers against a blind GPT review of random UK articles.

This is a research analysis. GPT opinions are not independent human labels.
"""

import csv
import hashlib
import json
import warnings
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.preprocessing import normalize


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results/uk-random-shadow-v1"
MODELS = {
    "original": ROOT / "results/finalist-models-v1/finalist-bundle.joblib",
    "updated": ROOT / "results/uk-diversity-candidate-v1/experimental-bundle.joblib",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def scores(bundle, articles):
    result = []
    for start in range(0, len(articles), 50):
        texts = [(a["title"] + "\n\n" + a["body"]).strip() for a in articles[start:start + 50]]
        matrix = hstack(
            [bundle["word_vectorizer"].transform(texts), bundle["char_vectorizer"].transform(texts)],
            format="csr", dtype=np.float32,
        )
        reduced = normalize(bundle["lsa200_svd"].transform(matrix)).astype(np.float32)
        result.extend(map(float, bundle["lsa200_rbf_c3"].decision_function(reduced)))
    return result


def metrics(rows, name, group):
    tp = sum(r["gpt_label"] == 1 and r[name + "_prediction"] == 1 for r in rows)
    tn = sum(r["gpt_label"] == 0 and r[name + "_prediction"] == 0 for r in rows)
    fp = sum(r["gpt_label"] == 0 and r[name + "_prediction"] == 1 for r in rows)
    fn = sum(r["gpt_label"] == 1 and r[name + "_prediction"] == 0 for r in rows)
    n = len(rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return dict(group=group, model=name, n=n, gpt_positive=tp + fn,
                tp=tp, tn=tn, fp=fp, fn=fn, accuracy=(tp + tn) / n if n else 0.0,
                precision=precision, recall=recall,
                f1=2 * precision * recall / (precision + recall) if precision + recall else 0.0)


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    blind = load_jsonl(OUT / "blind.jsonl")
    review = load_jsonl(OUT / "gpt-review.jsonl")
    with (OUT / "selection-internal.csv").open(encoding="utf-8-sig", newline="") as stream:
        internal = list(csv.DictReader(stream))
    if manifest["status"] != "frozen_blind_gpt_review_complete_before_model_reveal":
        raise ValueError("Blind GPT review must be completed before model scoring")
    if digest(OUT / "blind.jsonl") != manifest["blind_sha256"]:
        raise ValueError("Frozen blind packet changed")
    if len(blind) != len(review) or len(blind) != len(internal) or len(blind) != 117:
        raise ValueError("Packet, review, and metadata counts differ")
    if [a["article_id"] for a in blind] != [r["article_id"] for r in review]:
        raise ValueError("Review order differs from packet")
    if [a["article_id"] for a in blind] != [r["article_id"] for r in internal]:
        raise ValueError("Metadata order differs from packet")
    if any(r["label"] == 1 and (not r["evidence"] or r["evidence"] not in a["body"])
           for a, r in zip(blind, review)):
        raise ValueError("A positive GPT opinion lacks exact full-text evidence")
    model_scores = {}
    for name, path in MODELS.items():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            bundle = joblib.load(path)
        model_scores[name] = scores(bundle, blind)
    rows = []
    for i, (article, opinion, source) in enumerate(zip(blind, review, internal)):
        row = dict(article_id=article["article_id"], title=article["title"],
                   sample_arm=source["sample_arm"], url_section=source["url_section"],
                   source_url=source["source_url"], gpt_label=opinion["label"],
                   gpt_uncertain=opinion["uncertain"], gpt_evidence=opinion["evidence"],
                   gpt_reason=opinion["reason"])
        for name, values in model_scores.items():
            row[name + "_score"] = values[i]
            row[name + "_prediction"] = int(values[i] >= 0.0)
        rows.append(row)
    write_csv(OUT / "comparison.csv", rows)
    groups = {"all_gpt_opinions": rows,
              "certain_gpt_opinions": [r for r in rows if not r["gpt_uncertain"]]}
    for arm in ("all_time", "recent_12_months"):
        groups[arm] = [r for r in rows if r["sample_arm"] == arm]
        groups[arm + "_certain"] = [r for r in rows if r["sample_arm"] == arm and not r["gpt_uncertain"]]
    summary = [metrics(group_rows, name, group_name)
               for group_name, group_rows in groups.items() for name in MODELS]
    write_csv(OUT / "metrics.csv", summary)
    result = dict(status="shadow_scored_against_gpt_opinions_not_production_accuracy",
                  review_sha256=digest(OUT / "gpt-review.jsonl"),
                  original_bundle_sha256=digest(MODELS["original"]),
                  updated_bundle_sha256=digest(MODELS["updated"]),
                  total=len(rows), uncertain=sum(r["gpt_uncertain"] for r in rows),
                  gpt_label_counts=dict(Counter(r["gpt_label"] for r in rows)),
                  changed_predictions=sum(r["original_prediction"] != r["updated_prediction"] for r in rows),
                  comparison_sha256=digest(OUT / "comparison.csv"),
                  metrics_sha256=digest(OUT / "metrics.csv"))
    (OUT / "comparison-manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": result, "metrics": summary}, indent=2))


if __name__ == "__main__":
    main()
