"""Freeze a 400-article blind UK_GOV release-gate packet from the new export.

The SQL result is already in deterministic random order. This script removes
prior IDs and exact normalized-body overlaps, then takes the first 400 usable
rows without seeing labels or model scores. It never truncates article text.
"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from contracts import digest, file_digest, normalized_body
from prepare_pattern_training_v2 import ROOT


DEFAULT_EXPORT = Path("C:/Users/admin/Desktop/data-final_new_data.csv")
DEFAULT_OUTPUT = ROOT / "results/uk-release-gate-400-v1"
PACKETS = [
    ROOT / "results/source-stage-v1/training-blind.jsonl",
    ROOT / "results/source-stage-v1/reference-blind.jsonl",
    ROOT / "results/pattern-training-v1/blind.jsonl",
    ROOT / "results/pattern-training-v2/blind.jsonl",
    ROOT / "results/focus-review-v1/blind.jsonl",
    ROOT / "results/focus-review-v2/blind.jsonl",
    ROOT / "results/uk-diversity-review-v1/blind.jsonl",
    ROOT / "results/uk-diversity-training-v1/blind.jsonl",
    ROOT / "results/uk-random-shadow-v1/blind.jsonl",
    ROOT / "results/uk-followup-v1/training-blind.jsonl",
    ROOT / "results/uk-followup-v1/evaluation-blind.jsonl",
]


def jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path, nargs="?", default=DEFAULT_EXPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Frozen packet exists; do not overwrite")
    csv.field_size_limit(100_000_000)
    with args.export.open(encoding="utf-8-sig", newline="") as stream:
        exported = list(csv.DictReader(stream))
    required = {"media_record_id", "vv_media_id", "version_no", "published_date", "publisher_name", "source_url", "media_payload"}
    if len(exported) != 500 or any(not required.issubset(row) for row in exported):
        raise ValueError("Expected 500 rows with release-gate query fields")
    if len({row["vv_media_id"] for row in exported}) != len(exported):
        raise ValueError("Duplicate logical media IDs in export")
    old_ids = set()
    with (ROOT / "data/raw/adverse-news-uk-sample-5000.csv").open(encoding="utf-8-sig", newline="") as stream:
        old_ids = {row["vv_media_id"] for row in csv.DictReader(stream)}
    old_corpus = jsonl(ROOT / "data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl")
    prior_ids = {article["article_id"] for path in PACKETS for article in jsonl(path)}
    prior_bodies = {digest(normalized_body(article["body"])) for article in old_corpus if article["body"].strip()}
    prior_bodies.update(digest(normalized_body(article["body"])) for path in PACKETS
                        for article in jsonl(path) if article["body"].strip())
    excluded = Counter()
    seen_bodies = set()
    selected = []
    for order, row in enumerate(exported, start=1):
        if len(selected) == 400:
            break
        article_id = "db:" + row["media_record_id"]
        if row["vv_media_id"] in old_ids or article_id in prior_ids:
            excluded["prior_id"] += 1
            continue
        payload = json.loads(row["media_payload"])
        content = payload.get("Content") or {}
        title, body = content.get("Title"), content.get("BodyText")
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str) or len(body.strip()) < 300:
            excluded["unusable_title_or_body"] += 1
            continue
        body_hash = digest(normalized_body(body))
        if body_hash in prior_bodies:
            excluded["prior_body_overlap"] += 1
            continue
        if body_hash in seen_bodies:
            excluded["duplicate_body_within_export"] += 1
            continue
        seen_bodies.add(body_hash)
        selected.append((dict(article_id=article_id, title=title, body=body),
                         dict(article_id=article_id, export_order=order,
                              vv_media_id=row["vv_media_id"], version_no=row["version_no"],
                              published_date=row["published_date"], publisher_name=row["publisher_name"],
                              source_url=row["source_url"], body_hash=body_hash)))
    if len(selected) != 400:
        raise ValueError(f"Only {len(selected)} eligible articles after exclusions")
    args.output.mkdir(parents=True)
    blind = args.output / "blind.jsonl"
    with blind.open("x", encoding="utf-8", newline="\n") as stream:
        for article, _ in selected:
            stream.write(json.dumps(article, ensure_ascii=False) + "\n")
    internal = args.output / "selection-internal.csv"
    with internal.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(selected[0][1]))
        writer.writeheader()
        writer.writerows(meta for _, meta in selected)
    manifest = dict(status="frozen_blind_unreviewed", source_export_sha256=file_digest(args.export),
                    export_rows=len(exported), selected_count=len(selected),
                    exported_rows_consumed=selected[-1][1]["export_order"],
                    excluded_before_400=dict(excluded),
                    distinct_publisher_names=len({meta["publisher_name"] for _, meta in selected}),
                    blind_sha256=file_digest(blind), internal_sha256=file_digest(internal),
                    no_model_scores_or_labels_used_in_selection=True,
                    full_text_preserved=True,
                    independent_human_reference=False)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
