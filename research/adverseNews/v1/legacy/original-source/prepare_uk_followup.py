"""Freeze disjoint UK training and review packets from the earlier 1,500-row export."""

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import joblib
import numpy as np

from contracts import digest, file_digest, normalized_body
from prepare_pattern_training_v2 import ROOT, score
from prepare_uk_diversity_review import section


EXPORT = Path("C:/Users/admin/Desktop/data-new-adverse.csv")
OUT = ROOT / "results/uk-followup-v1"
OLD = ROOT / "data/raw/adverse-news-uk-sample-5000.csv"
BASE = ROOT / "results/finalist-models-v1/finalist-bundle.joblib"
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
]
KEYWORDS = {
    "violence_buried": ("murder", "murdered", "arson", "bomb", "killed", "attack"),
    "rights_abuse": ("torture", "forced labour", "forced labor", "inhumane", "prison camp"),
    "sanctions_evasion": ("sanctions", "sanction", "evasion", "weapons programme"),
    "medicine_illegal": ("illegal medicines", "unauthorised medicines", "counterfeit medicines", "illegal sale", "illegally supplied"),
}
SECTION_QUOTA = {
    "government/news": 7,
    "government/publications": 5,
    "government/case-studies": 5,
    "government/speeches": 5,
    "government/fatalities": 4,
    "drug-safety-update": 4,
    "drug-device-alerts": 5,
}


def jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]


def order(article, salt):
    return hashlib.sha256((salt + "|" + article["article_id"]).encode()).hexdigest()


def write_jsonl(path, articles):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for article in articles:
            stream.write(json.dumps({k: article[k] for k in ("article_id", "title", "body")}, ensure_ascii=False) + "\n")


def main():
    if OUT.exists():
        raise ValueError("Frozen output exists")
    csv.field_size_limit(100_000_000)
    if file_digest(EXPORT) != "02ec40654489f076dcee6dd3359f86277eff178c4f43fa939b8e7821de18e51f":
        raise ValueError("Source export changed")
    old_ids = set()
    with OLD.open(encoding="utf-8-sig", newline="") as stream:
        old_ids.update(r["vv_media_id"] for r in csv.DictReader(stream))
    corpus = jsonl(ROOT / "data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl")
    old_content = {r["content_hash"] for r in corpus}
    old_bodies = {digest(normalized_body(r["body"])) for r in corpus}
    reserved = {r["article_id"] for path in PACKETS for r in jsonl(path)}
    with EXPORT.open(encoding="utf-8-sig", newline="") as stream:
        source = list(csv.DictReader(stream))
    candidates = []
    excluded = Counter()
    for row in source:
        payload = json.loads(row["media_payload"])
        content = payload.get("Content") or {}
        title, body = content.get("Title"), content.get("BodyText")
        article_id = "db:" + row["media_record_id"]
        if not isinstance(title, str) or not isinstance(body, str) or len(body) < 300 or len(body) > 12000:
            excluded["unusable_or_length"] += 1
            continue
        if row["vv_media_id"] in old_ids or article_id in reserved:
            excluded["previously_used_or_reserved"] += 1
            continue
        if digest([title, body]) in old_content or digest(normalized_body(body)) in old_bodies:
            excluded["original_text_overlap"] += 1
            continue
        candidates.append(dict(article_id=article_id, title=title, body=body,
                               section=section(row["source_url"]), source_url=row["source_url"],
                               publisher=(payload.get("Publisher") or {}).get("Name") or ""))
    # First reserve evaluation articles by a deterministic hash; this uses no
    # model score, labels, keywords, or results from the previous random check.
    evaluation = []
    selected_ids = set()
    for sec, quota in SECTION_QUOTA.items():
        pool = sorted((a for a in candidates if a["section"] == sec),
                      key=lambda a: order(a, "uk-followup-evaluation-v1"))
        choices = pool[:quota]
        if len(choices) != quota:
            raise ValueError("Insufficient evaluation articles in " + sec)
        evaluation.extend(choices)
        selected_ids.update(a["article_id"] for a in choices)
    evaluation.sort(key=lambda a: order(a, "uk-followup-eval-blind-order"))
    remaining = [a for a in candidates if a["article_id"] not in selected_ids]
    # The original model is used only to enrich training with likely misses;
    # its scores are never used to choose the reserved evaluation packet.
    with np.errstate(all="ignore"):
        values = score(joblib.load(BASE), remaining)
    for article, value in zip(remaining, values):
        article["original_score"] = value
    training = []
    purposes = []
    train_ids = set()
    for name, words in KEYWORDS.items():
        pool = [a for a in remaining if a["original_score"] < 0 and
                any(word in a["body"].lower() for word in words)]
        pool.sort(key=lambda a: (abs(a["original_score"]), order(a, name)))
        choices = [a for a in pool if a["article_id"] not in train_ids][:5]
        if not choices:
            raise ValueError("No training articles in " + name)
        for article in choices:
            training.append(article)
            purposes.append(name)
            train_ids.add(article["article_id"])
    for sec in SECTION_QUOTA:
        pool = [a for a in remaining if a["section"] == sec and a["article_id"] not in train_ids]
        pool.sort(key=lambda a: (abs(a["original_score"]), order(a, "uk-followup-matched")))
        choices = pool[:3]
        if len(choices) != 3:
            raise ValueError("Insufficient matched articles in " + sec)
        for article in choices:
            training.append(article)
            purposes.append("section_control_" + sec)
            train_ids.add(article["article_id"])
    pairs = sorted(zip(training, purposes), key=lambda pair: order(pair[0], "uk-followup-training-blind-order"))
    OUT.mkdir(parents=True)
    write_jsonl(OUT / "evaluation-blind.jsonl", evaluation)
    write_jsonl(OUT / "training-blind.jsonl", [a for a, _ in pairs])
    with (OUT / "selection-internal.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        fields = ("article_id", "packet", "purpose", "section", "publisher", "source_url", "original_score")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for article in evaluation:
            writer.writerow(dict(article_id=article["article_id"], packet="evaluation", purpose="source_section_random",
                                 section=article["section"], publisher=article["publisher"],
                                 source_url=article["source_url"], original_score=""))
        for article, purpose in pairs:
            writer.writerow(dict(article_id=article["article_id"], packet="training", purpose=purpose,
                                 section=article["section"], publisher=article["publisher"],
                                 source_url=article["source_url"], original_score=article["original_score"]))
    summary = dict(status="frozen_unreviewed", source_sha256=file_digest(EXPORT),
                   source_rows=len(source), available_candidates=len(candidates), excluded=dict(excluded),
                   evaluation_count=len(evaluation), training_count=len(pairs),
                   evaluation_sha256=file_digest(OUT / "evaluation-blind.jsonl"),
                   training_sha256=file_digest(OUT / "training-blind.jsonl"),
                   source_section_quota=SECTION_QUOTA, training_purposes=dict(Counter(purposes)),
                   model_scores_used_for_evaluation_selection=False,
                   source_random_evaluation_is_population_representative=False)
    (OUT / "manifest.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
