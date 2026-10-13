"""Measure how much of the final 900-record labels the lookup rules alone produce, per label, part and source."""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from buildDataset import find_canonical
from lookupRules import record_rule_labels

RESEARCH = Path(__file__).resolve().parents[1]
DATA, RESULTS = RESEARCH / "data", RESEARCH / "results"


def load():
    """Final records, label names and the labels each record had before lookup rules were applied."""
    records = [json.loads(line) for line in open(DATA / "dataset_900.jsonl", encoding="utf-8")]
    names = json.load(open(DATA / "label_names.json", encoding="utf-8"))
    added = defaultdict(set)
    with open(DATA / "label_changes.csv", encoding="utf-8-sig", newline="") as f:
        for c in csv.DictReader(f):
            if c["action"] == "add":
                added[c["record_key"]].add(c["label"])
    base = {r["record_key"]: set(r["labels"]) - added[r["record_key"]] for r in records}
    return records, names, base


def pct(a, b):
    """Percentage string, or blank when the denominator is zero."""
    return f"{100 * a / b:.1f}" if b else ""


def main():
    records, names, base = load()
    canonical = find_canonical(records)
    lookup = {r["record_key"]: set(record_rule_labels(r["source"], canonical[r["record_key"]])) for r in records}

    per_label = []
    for n in names:
        pos = [r for r in records if n in r["labels"]]
        given = [r for r in records if n in lookup[r["record_key"]]]
        agree_base = [r for r in given if n in base[r["record_key"]]]
        per_label.append([n, len(pos), len(given), pct(len(given), len(pos)), len(pos) - len(given),
                          pct(len(agree_base), len(given))])

    def summary(rows):
        inst = sum(len(r["labels"]) for r in rows)
        cov = sum(len(lookup[r["record_key"]] & set(r["labels"])) for r in rows)
        full = sum(1 for r in rows if set(r["labels"]) <= lookup[r["record_key"]])
        text_needed = sum(1 for r in rows if set(r["labels"]) - lookup[r["record_key"]])
        empty = sum(1 for r in rows if not r["labels"])
        return [len(rows), inst, cov, pct(cov, inst), full, text_needed, pct(text_needed, len(rows)), empty]

    by_part = [[p] + summary([r for r in records if r["part"] == p]) for p in sorted({r["part"] for r in records})]
    by_part.append(["ALL"] + summary(records))
    by_source = [[s] + summary([r for r in records if r["source"] == s]) for s in sorted({r["source"] for r in records})]

    RESULTS.mkdir(exist_ok=True)
    head = ["records", "label_instances", "from_lookup", "pct_from_lookup", "records_fully_from_lookup",
            "records_needing_text", "pct_needing_text", "records_without_labels"]
    for name, header, rows in [
        ("lookupCoverage_by_label.csv", ["label", "positives", "from_lookup", "pct_from_lookup", "left_for_text",
                                         "pct_lookup_agrees_with_labellers"], per_label),
        ("lookupCoverage_by_part.csv", ["part"] + head, by_part),
        ("lookupCoverage_by_source.csv", ["source"] + head, by_source),
    ]:
        with open(RESULTS / name, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
    for row in by_part:
        print(row)
    for row in per_label:
        print(row)


if __name__ == "__main__":
    main()
