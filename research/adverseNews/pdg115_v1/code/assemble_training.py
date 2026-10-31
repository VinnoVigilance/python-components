"""Rebuild the frozen 5,044-row research training set from compact inputs.

Run from any working directory. This creates a new file and never overwrites an
existing training snapshot. Model features remain title and full body only.
"""

import csv
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "training"


def jsonl(path):
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rt", encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reviewed_additions(name):
    articles = jsonl(TRAIN / f"{name}-blind.jsonl")
    reviews = jsonl(TRAIN / f"{name}-review.jsonl")
    if [item["article_id"] for item in articles] != [item["article_id"] for item in reviews]:
        raise ValueError(f"{name}: article/review order differs")
    for article, review in zip(articles, reviews):
        if review["label"] not in (0, 1):
            raise ValueError(f"{name}: invalid label")
        if review["label"] == 1 and (
            not review["evidence"] or review["evidence"] not in article["body"]
        ):
            raise ValueError(f"{name}: positive evidence absent")
    return [dict(article, label=review["label"], label_origin=f"{name}-gpt-review")
            for article, review in zip(articles, reviews) if not review["uncertain"]]


def main():
    output = TRAIN / "final-5044.jsonl.gz"
    manifest_path = TRAIN / "manifest.json"
    if output.exists() or manifest_path.exists():
        raise ValueError("Frozen training output exists; do not overwrite")
    base = jsonl(TRAIN / "base-5000.jsonl.gz")
    if len(base) != 5000:
        raise ValueError("Base corpus must contain 5,000 rows")
    by_id = {row["article_id"]: row for row in base}
    if len(by_id) != len(base):
        raise ValueError("Duplicate article ID in base corpus")
    with (TRAIN / "uk-notice-policy-corrections.csv").open(encoding="utf-8-sig", newline="") as stream:
        corrections = list(csv.DictReader(stream))
    for correction in corrections:
        row = by_id[correction["article_id"]]
        if row["label"] != int(correction["old_silver_label"]):
            raise ValueError("Stale policy correction")
        row["label"] = int(correction["policy_label"])
        row["label_origin"] = "product-confirmed-uk-notice-policy-correction"
    earlier = reviewed_additions("earlier-additions")
    new = reviewed_additions("new-additions")
    if len(corrections) != 6 or len(earlier) != 18 or len(new) != 26:
        raise ValueError("Unexpected correction or addition count")
    rows = base + earlier + new
    if len(rows) != 5044 or len({row["article_id"] for row in rows}) != 5044:
        raise ValueError("Final training count or IDs differ")
    with output.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            for row in rows:
                compressed.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    manifest = {
        "status": "frozen_training_snapshot",
        "rows": len(rows),
        "positive": sum(row["label"] == 1 for row in rows),
        "negative": sum(row["label"] == 0 for row in rows),
        "base_rows": len(base),
        "policy_corrections": len(corrections),
        "earlier_certain_additions": len(earlier),
        "new_certain_additions": len(new),
        "base_sha256": sha256(TRAIN / "base-5000.jsonl.gz"),
        "final_sha256": sha256(output),
        "label_policy": "adverse-news-policy-v0.2",
        "independent_human_reference": False,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
