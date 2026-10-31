"""Score a completed blind GPT-review packet without changing training data.

The fresh_400_in_progress packet is rejected until all 400 reviews exist.
Results measure agreement with GPT opinions, not independent human accuracy.
"""

import argparse
import hashlib
import json
from pathlib import Path

import joblib

from score_articles import DEFAULT_MODEL, scores


PACKETS = {
    "fresh_35": (35, "review.jsonl"),
    "diagnostic_117": (117, "review.jsonl"),
    "mixed_150": (150, "review.jsonl"),
    "fresh_400_in_progress": (400, "review-partial.jsonl"),
}
ROOT = Path(__file__).resolve().parents[1]


def jsonl(path):
    with path.open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", choices=PACKETS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists")
    expected, review_name = PACKETS[args.packet]
    directory = ROOT / "evaluation" / args.packet
    blind_path, review_path = directory / "blind.jsonl", directory / review_name
    articles, reviews = jsonl(blind_path), jsonl(review_path)
    if len(articles) != expected or len(reviews) != expected:
        raise ValueError(f"Packet needs {expected} complete blind articles and reviews")
    if [article["article_id"] for article in articles] != [review["article_id"] for review in reviews]:
        raise ValueError("Blind packet and review IDs or order differ")
    for article, review in zip(articles, reviews):
        if review["label"] not in (0, 1):
            raise ValueError("Invalid binary review label")
        if review["label"] == 1 and (
            not review["evidence"] or not any(review["evidence"] in article[field]
                                              for field in ("title", "body"))
        ):
            raise ValueError("Positive review evidence missing from article")
    bundle = joblib.load(DEFAULT_MODEL)
    predictions = [value >= 0 for value in scores(bundle, articles)]
    tp = sum(review["label"] == 1 and prediction for review, prediction in zip(reviews, predictions))
    tn = sum(review["label"] == 0 and not prediction for review, prediction in zip(reviews, predictions))
    fp = sum(review["label"] == 0 and prediction for review, prediction in zip(reviews, predictions))
    fn = sum(review["label"] == 1 and not prediction for review, prediction in zip(reviews, predictions))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    report = {
        "packet": args.packet,
        "reference": "GPT full-text opinions, not independent human labels",
        "articles": expected,
        "gpt_positive": tp + fn,
        "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": (tp + tn) / expected,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "blind_sha256": hashlib.sha256(blind_path.read_bytes()).hexdigest(),
        "review_sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
