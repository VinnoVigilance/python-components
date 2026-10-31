"""Refit an isolated UK candidate and compare on frozen GPT-review packets."""

import csv
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn import __version__ as sklearn_version
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from sklearn.svm import SVC
from threadpoolctl import threadpool_limits

from contracts import file_digest
from run_classical_screen import tfidf_char, tfidf_word
from package_uk_diversity_candidate import ROOT, OLD, CORRECTIONS, TRAIN, score


OUT = ROOT / "results/uk-followup-candidate-v1"
FOLLOW = ROOT / "results/uk-followup-v1"
RANDOM = ROOT / "results/uk-random-shadow-v1"
BASE = ROOT / "results/finalist-models-v1/finalist-bundle.joblib"
EXPERIMENTAL = ROOT / "results/uk-diversity-candidate-v1/experimental-bundle.joblib"


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line]


def metrics(opinions, values):
    certain = [(o, v) for o, v in zip(opinions, values) if not o["uncertain"]]
    return {
        name: make_metrics(pairs)
        for name, pairs in (("all", list(zip(opinions, values))), ("certain", certain))
    }


def make_metrics(pairs):
    tp = sum(o["label"] == 1 and v >= 0 for o, v in pairs)
    tn = sum(o["label"] == 0 and v < 0 for o, v in pairs)
    fp = sum(o["label"] == 0 and v >= 0 for o, v in pairs)
    fn = sum(o["label"] == 1 and v < 0 for o, v in pairs)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return dict(n=len(pairs), gpt_positive=tp + fn, tp=tp, tn=tn, fp=fp, fn=fn,
                accuracy=(tp + tn) / len(pairs) if pairs else 0.0,
                precision=precision, recall=recall,
                f1=2 * precision * recall / (precision + recall) if precision + recall else 0.0)


def main():
    if OUT.exists():
        raise ValueError("Candidate output already exists")
    follow_manifest = json.loads((FOLLOW / "manifest.json").read_text(encoding="utf-8"))
    training_packet = jsonl(FOLLOW / "training-blind.jsonl")
    training_opinions = jsonl(FOLLOW / "training-gpt-review.jsonl")
    eval_packet = jsonl(FOLLOW / "evaluation-blind.jsonl")
    eval_opinions = jsonl(FOLLOW / "evaluation-gpt-review.jsonl")
    if file_digest(FOLLOW / "training-blind.jsonl") != follow_manifest["training_sha256"]:
        raise ValueError("Training packet changed")
    if file_digest(FOLLOW / "evaluation-blind.jsonl") != follow_manifest["evaluation_sha256"]:
        raise ValueError("Evaluation packet changed")
    if len(training_packet) != 36 or len(training_opinions) != 36:
        raise ValueError("Training review count mismatch")
    if len(eval_packet) != 35 or len(eval_opinions) != 35:
        raise ValueError("Evaluation review count mismatch")
    for packet, opinions in ((training_packet, training_opinions), (eval_packet, eval_opinions)):
        if [a["article_id"] for a in packet] != [o["article_id"] for o in opinions]:
            raise ValueError("Packet/review order mismatch")
        if any(o["label"] == 1 and (not o["evidence"] or o["evidence"] not in a["body"])
               for a, o in zip(packet, opinions)):
            raise ValueError("Missing exact positive evidence")
    existing = jsonl(OLD)
    if len(existing) != 5000:
        raise ValueError("Original corpus changed")
    by_id = {a["article_id"]: a for a in existing}
    with CORRECTIONS.open(encoding="utf-8-sig", newline="") as stream:
        corrections = list(csv.DictReader(stream))
    for correction in corrections:
        target = by_id[correction["article_id"]]
        if target["label"] != int(correction["old_silver_label"]):
            raise ValueError("Stale correction")
        target["label"] = int(correction["policy_label"])
    previous_packet = jsonl(TRAIN / "blind.jsonl")
    previous_opinions = jsonl(TRAIN / "gpt-review.jsonl")
    if [a["article_id"] for a in previous_packet] != [o["article_id"] for o in previous_opinions]:
        raise ValueError("Previous training mismatch")
    previous_additions = [dict(a, label=o["label"]) for a, o in zip(previous_packet, previous_opinions) if not o["uncertain"]]
    new_additions = [dict(a, label=o["label"]) for a, o in zip(training_packet, training_opinions) if not o["uncertain"]]
    assert len(previous_additions) == 18 and len(new_additions) == 26
    # The returned original corpus objects are local copies; no source file is modified.
    training = existing + previous_additions + new_additions
    texts = [(a["title"] + "\n\n" + a["body"]).strip() for a in training]
    labels = np.asarray([a["label"] for a in training], dtype=int)
    with threadpool_limits(limits=4):
        word, char = tfidf_word(), tfidf_char()
        matrix = hstack([word.fit_transform(texts), char.fit_transform(texts)], format="csr", dtype=np.float32)
        svd = TruncatedSVD(n_components=200, n_iter=5, random_state=115)
        reduced = normalize(svd.fit_transform(matrix)).astype(np.float32)
        model = SVC(C=3, kernel="rbf", gamma="scale", class_weight="balanced")
        model.fit(reduced, labels)
        bundle = dict(word_vectorizer=word, char_vectorizer=char,
                      lsa200_svd=svd, lsa200_rbf_c3=model,
                      default_thresholds={"lsa200_rbf_c3": 0.0}, model_inputs=["title", "body"])
    OUT.mkdir()
    artifact = OUT / "candidate-bundle.joblib"
    joblib.dump(bundle, artifact, compress=3)
    packets = {
        "fresh_section_reserved": (eval_packet, eval_opinions),
        "previous_random_diagnostic": (jsonl(RANDOM / "blind.jsonl"), jsonl(RANDOM / "gpt-review.jsonl")),
        "older_reserved": (jsonl(ROOT / "results/source-stage-v1/reference-blind.jsonl"),
                           jsonl(ROOT / "results/source-stage-v1/returns/astra-medium-reference.jsonl")),
    }
    old = joblib.load(BASE)
    prior = joblib.load(EXPERIMENTAL)
    summaries = {}
    rows = []
    for packet_name, (articles, opinions) in packets.items():
        if [a["article_id"] for a in articles] != [o["article_id"] for o in opinions]:
            raise ValueError("Evaluation packet mismatch: " + packet_name)
        values = {"original": score(old, articles),
                  "previous_experiment": score(prior, articles),
                  "new_candidate": score(bundle, articles)}
        summaries[packet_name] = {name: metrics(opinions, values[name]) for name in values}
        for i, article in enumerate(articles):
            rows.append(dict(packet=packet_name, article_id=article["article_id"], title=article["title"],
                             gpt_label=opinions[i]["label"], gpt_uncertain=opinions[i]["uncertain"],
                             gpt_evidence=opinions[i]["evidence"],
                             **{name + "_score": values[name][i] for name in values},
                             **{name + "_prediction": int(values[name][i] >= 0) for name in values}))
    with (OUT / "comparison.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = dict(status="research_candidate_unpromoted", model="lsa200_rbf_c3", threshold=0.0,
                    sklearn_version=sklearn_version, original_rows=5000, policy_corrections=len(corrections),
                    earlier_certain_additions=len(previous_additions), new_certain_additions=len(new_additions),
                    new_uncertain_excluded=sum(o["uncertain"] for o in training_opinions),
                    total_training_rows=len(training),
                    training_packet_sha256=file_digest(FOLLOW / "training-blind.jsonl"),
                    training_review_sha256=file_digest(FOLLOW / "training-gpt-review.jsonl"),
                    evaluation_packet_sha256=file_digest(FOLLOW / "evaluation-blind.jsonl"),
                    evaluation_review_sha256=file_digest(FOLLOW / "evaluation-gpt-review.jsonl"),
                    candidate_bundle_sha256=file_digest(artifact),
                    comparison_sha256=file_digest(OUT / "comparison.csv"),
                    independent_human_reference=False, scores=summaries)
    def number(value):
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        raise TypeError(type(value).__name__)

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, default=number) + "\n", encoding="utf-8")
    print(json.dumps({"training_rows": len(training), "scores": summaries}, indent=2, default=number))


if __name__ == "__main__":
    main()
