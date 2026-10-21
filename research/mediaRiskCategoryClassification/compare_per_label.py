"""Compare saved Validation predictions per label; never train a model."""
import argparse
import csv
import json
from pathlib import Path

REMOVED_LABELS = ("PEP", "Crime-Organized Crime", "Crime-Theft & Property Crime")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def label_set(value):
    return set(filter(None, value.split(", ")))


def index_rows(rows):
    indexed = {row["news_id"]: row for row in rows}
    if not rows or len(indexed) != len(rows):
        raise ValueError("empty file or duplicate news_id")
    return indexed


def compare_run(directory, reference, labels):
    meta = json.loads((directory / "run.json").read_text(encoding="utf-8-sig"))
    if meta.get("status") != "completed":
        raise ValueError("run is not completed")
    if not set(labels).issubset(meta.get("labels", [])):
        raise ValueError("run does not cover all compared labels")
    predictions = index_rows(read_csv(directory / "validation_predictions.csv"))
    if predictions.keys() != reference.keys():
        raise ValueError("Validation article IDs differ")
    counts = {label: {"tp": 0, "fp": 0, "fn": 0} for label in labels}
    for news_id, source in reference.items():
        row = predictions[news_id]
        if any(row[key] != source[key] for key in ("title", "body")):
            raise ValueError(f"Validation article text differs: {news_id}")
        true = label_set(row["true_labels"])
        predicted = label_set(row["predicted_labels"])
        if not (true | predicted).issubset(set(meta["labels"])):
            raise ValueError("prediction file contains undeclared labels")
        for label in labels:
            if source[label] not in ("0", "1"):
                raise ValueError("current references must be binary")
            positive = source[label] == "1"
            if (label in true) != positive:
                raise ValueError(f"Validation reference differs: {news_id}, {label}")
            counts[label]["tp"] += int(positive and label in predicted)
            counts[label]["fp"] += int(not positive and label in predicted)
            counts[label]["fn"] += int(positive and label not in predicted)
    results = []
    for label, count in counts.items():
        tp, fp, fn = (count[key] for key in ("tp", "fp", "fn"))
        results.append({
            "label": label, "model": meta["model_name"], "run_id": directory.name,
            "split": "validation", "support": tp + fn, **count,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        })
    return meta["model_name"], results


def historical_reference(directories, reference, active_labels):
    """Recover removed references from one compatible historical run, in memory."""
    for directory in directories:
        try:
            compare_run(directory, reference, active_labels)
            meta = json.loads((directory / "run.json").read_text(encoding="utf-8-sig"))
            if not set(REMOVED_LABELS).issubset(meta["labels"]):
                continue
            predictions = index_rows(read_csv(directory / "validation_predictions.csv"))
            restored = {}
            for news_id, row in reference.items():
                true = label_set(predictions[news_id]["true_labels"])
                restored[news_id] = {**row, **{label: str(int(label in true))
                                            for label in REMOVED_LABELS}}
            return restored, directory.name
        except (OSError, ValueError, KeyError, TypeError):
            continue
    raise ValueError("No compatible completed historical run contains all three removed labels. "
                     "Copy the original historical run folders into outputs/; no retraining is needed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent,
                        help="Project folder containing data/ and outputs/ (default: script folder)")
    parser.add_argument("--removed", action="store_true",
                        help="Compare PEP, Organized Crime and Theft using saved historical references")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = json.loads((root / "data" / "manifest.json").read_text(encoding="utf-8-sig"))
    active_labels = manifest["labels"]
    labels = list(REMOVED_LABELS) if args.removed else active_labels
    reference = index_rows(read_csv(root / "data" / "validation.csv"))
    for row in reference.values():
        if row.get("split") != "validation":
            raise ValueError("current validation.csv contains a different split")
    directories = [directory for directory in sorted((root / "outputs").iterdir())
                   if directory.is_dir() and (directory / "run.json").is_file()]
    comparison_reference = reference
    if args.removed:
        comparison_reference, reference_run = historical_reference(directories, reference, active_labels)
        print(f"Historical removed-label reference source: {reference_run}")
        print("Current data, label scope and model files are unchanged.")
    latest, skipped = {}, []
    for directory in directories:
        try:
            if args.removed:
                compare_run(directory, reference, active_labels)
            model, rows = compare_run(directory, comparison_reference, labels)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            skipped.append((directory.name, str(exc)))
            continue
        latest[model] = rows
    if not latest:
        for run_id, reason in skipped:
            print(f"Skipped {run_id}: {reason}")
        raise SystemExit("No compatible completed runs with full Validation predictions found.")
    print(f"Validation: {len(reference)} identical articles; {len(labels)} compared labels.")
    print("Latest compatible completed run per model; ranking by F1, then Precision, Recall.")
    print("All included runs have identical article text and references for the compared labels.")
    print("This is an exploratory comparison, not an evaluation of a combined model.\n")
    for model, rows in sorted(latest.items()):
        print(f"{model}: {rows[0]['run_id']}")
    print(f"\n{'Label':<39} {'Model':<30} {'P':>7} {'R':>7} {'F1':>7}")
    report = []
    for label in labels:
        ranked = sorted((row for rows in latest.values() for row in rows if row["label"] == label),
                        key=lambda row: (-row["f1"], -row["precision"], -row["recall"], row["model"]))
        for rank, row in enumerate(ranked, start=1):
            report.append({"rank": rank, **row})
            if rank <= 2:
                print(f"{label if rank == 1 else '':<39} {row['model']:<30} "
                      f"{row['precision']:>7.4f} {row['recall']:>7.4f} {row['f1']:>7.4f}")
    filename = "removed_labels_model_comparison.csv" if args.removed else "per_label_model_comparison.csv"
    path = root / "outputs" / filename
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(report[0]))
        writer.writeheader()
        writer.writerows(report)
    print(f"\nFull ranking saved: {path}")
    print(f"Included {len(latest)} models; skipped {len(skipped)} incompatible/incomplete runs.")
    for run_id, reason in skipped:
        print(f"Skipped {run_id}: {reason}")
    if len(latest) == 1:
        print("Only one compatible model is available; no comparison between models is possible yet.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"ERROR: {exc}")
