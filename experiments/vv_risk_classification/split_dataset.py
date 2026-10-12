"""Freeze a reproducible 70/15/15 split of the prepared VV silver dataset.

Uses seeded SHA-256 ordering of body hashes, not label stratification or time.
Exact duplicate bodies must already be removed. Unknown labels stay -1.
Existing splits are verified and reused; they are never silently overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

try:
    from audit_dataset import LABEL_NAMES, file_hash
    from prepare_dataset import json_lines, write_atomic
except ModuleNotFoundError as error:
    if error.name not in {"audit_dataset", "prepare_dataset"}:
        raise
    raise SystemExit("ERROR: put audit_dataset.py, prepare_dataset.py and split_dataset.py in the same folder.") from error


ALGORITHM_VERSION = "seeded-body-hash-split-v1"
SPLIT_NAMES = ("train", "validation", "test")


def support_counts(rows: list[dict], labels: list[str]) -> dict:
    return {
        label: {
            "positive": sum(row["label_values"][index] == 1 for row in rows),
            "provisional_negative": sum(row["label_values"][index] == 0 for row in rows),
            "unknown": sum(row["label_values"][index] == -1 for row in rows),
        } for index, label in enumerate(labels)
    }


def support_warnings(support: dict, labels: list[str]) -> list[dict]:
    warnings = []
    for label in labels:
        train, validation, test = (support[name][label] for name in SPLIT_NAMES)
        reasons = []
        if not train["positive"] or not train["provisional_negative"]:
            reasons.append("training_requires_both_known_classes")
        if train["positive"] < 20:
            reasons.append("few_training_positives")
        if not validation["positive"]:
            reasons.append("validation_recall_not_estimable")
        if not test["positive"]:
            reasons.append("test_recall_not_estimable")
        if reasons:
            warnings.append({"label": label, "reasons": reasons,
                             "positive_counts": {name: support[name][label]["positive"] for name in SPLIT_NAMES}})
    return warnings


def print_summary(manifest: dict, output_dir: Path, reused: bool) -> None:
    print("Existing frozen split verified and reused." if reused else "New frozen split created.")
    print(f"Seed: {manifest['seed']}")
    print(f"Total prepared news: {manifest['input_rows']}")
    for name in SPLIT_NAMES:
        print(f"{name.capitalize()} news: {manifest['splits'][name]['rows']}")
    print("Record ID overlap: 0")
    print("Exact body overlap: 0")
    print("Unknown label values preserved: -1")
    print("Labels with low or missing support:")
    if not manifest["support_warnings"]:
        print("  None under the report's support checks.")
    for warning in manifest["support_warnings"]:
        counts = warning["positive_counts"]
        print(f"  {warning['label']}: train={counts['train']}, validation={counts['validation']}, test={counts['test']}")
    print(f"Files saved to: {output_dir}")
    print("Split completed. Test is reserved for final evaluation; model selection uses validation.")


def main() -> int:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=base / "data" / "processed")
    parser.add_argument("--output-dir", type=Path, default=base / "data" / "processed" / "splits")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        dataset_path = args.input_dir / "news_dataset.jsonl"
        labels_path = args.input_dir / "labels.json"
        preparation_path = args.input_dir / "preparation_report.json"
        for path in (dataset_path, labels_path, preparation_path):
            if not path.is_file():
                raise ValueError(f"Prepared file not found: {path}. Run prepare_dataset.py first.")
        preparation = json.loads(preparation_path.read_text(encoding="utf-8"))
        dataset_hash, labels_hash = file_hash(dataset_path), file_hash(labels_path)
        if dataset_hash != preparation["dataset_sha256"] or labels_hash != preparation["labels_sha256"]:
            raise ValueError("Prepared files differ from preparation_report.json; inspect the change before splitting.")
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        if labels != list(LABEL_NAMES):
            raise ValueError("Label names/order differ from the frozen 23-label sample schema.")

        manifest_path = args.output_dir / "split_manifest.json"
        if manifest_path.is_file():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (existing.get("algorithm_version") != ALGORITHM_VERSION
                    or existing.get("seed") != args.seed
                    or existing.get("input_dataset_sha256") != dataset_hash
                    or existing.get("input_labels_sha256") != labels_hash):
                raise ValueError("Frozen split configuration/input changed. Use a new versioned output directory; keep the previous split.")
            for name in SPLIT_NAMES:
                path = args.output_dir / f"{name}.jsonl"
                if not path.is_file() or file_hash(path) != existing["splits"][name]["sha256"]:
                    raise ValueError(f"Frozen {name} file is missing or changed. Restore the original frozen split before continuing.")
            print_summary(existing, args.output_dir, reused=True)
            return 0

        if any((args.output_dir / f"{name}.jsonl").exists() for name in SPLIT_NAMES):
            raise ValueError("Split files exist without a manifest. Inspect them or use a new output directory; files will not be overwritten.")
        records = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(records) != preparation["statistics"]["prepared_rows"] or len(records) < 10:
            raise ValueError("Unexpected prepared row count; inspect preparation_report.json.")
        identifiers, body_hashes, original_identifiers = set(), set(), set()
        for row in records:
            identifier, fingerprint = row.get("news_id"), row.get("body_hash")
            values = row.get("label_values", [])
            if not identifier or identifier in identifiers or not fingerprint or fingerprint in body_hashes:
                raise ValueError("Missing/duplicate news ID or body hash in prepared data.")
            if len(values) != len(labels) or any(type(value) is not int or value not in (-1, 0, 1) for value in values):
                raise ValueError(f"Invalid targets for {identifier}; expected 23 values in -1/0/1.")
            if not isinstance(row.get("text"), str) or not row["text"].strip():
                raise ValueError(f"Empty classifier text for {identifier}.")
            aliases = row.get("original_news_ids", [])
            if not aliases or len(set(aliases)) != len(aliases) or set(aliases) & original_identifiers:
                raise ValueError(f"Original ID lineage overlaps for {identifier}.")
            identifiers.add(identifier)
            body_hashes.add(fingerprint)
            original_identifiers.update(aliases)

        def ordering_key(row: dict) -> str:
            return hashlib.sha256(f"{ALGORITHM_VERSION}:{args.seed}:{row['body_hash']}".encode("utf-8")).hexdigest()

        ordered = sorted(records, key=ordering_key)
        validation_size = round(len(records) * 0.15)
        test_size = round(len(records) * 0.15)
        train_size = len(records) - validation_size - test_size
        partitions = {
            "train": ordered[:train_size],
            "validation": ordered[train_size:train_size + validation_size],
            "test": ordered[train_size + validation_size:],
        }
        support = {name: support_counts(rows, labels) for name, rows in partitions.items()}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        split_metadata = {}
        for name, rows in partitions.items():
            digest = write_atomic(args.output_dir / f"{name}.jsonl", json_lines(rows))
            split_metadata[name] = {"rows": len(rows), "sha256": digest,
                                    "news_ids": [row["news_id"] for row in rows]}
        manifest = {
            "schema_version": 1,
            "algorithm_version": ALGORITHM_VERSION,
            "seed": args.seed,
            "requested_ratios": {"train": 0.70, "validation": 0.15, "test": 0.15},
            "input_rows": len(records),
            "input_dataset_sha256": dataset_hash,
            "input_labels_sha256": labels_hash,
            "input_preprocessing_version": preparation["preprocessing_version"],
            "label_source": preparation["label_source"],
            "labels": labels,
            "target_encoding": preparation["target_encoding"],
            "feature_column": "text",
            "splits": split_metadata,
            "support_by_split": support,
            "support_warnings": support_warnings(support, labels),
            "checks": {"record_id_overlap": 0, "exact_body_overlap": 0, "original_id_overlap": 0},
            "limitations": [
                "Seeded random-style split, not multilabel stratification, temporal split or source holdout.",
                "Exact duplicate bodies and original IDs cannot overlap; near-duplicate/event leakage is not assessed.",
                "Silver-label evaluation measures agreement with consensus targets, not human-validated VV accuracy.",
                "A label without known positives cannot have recall estimated on that partition.",
            ],
        }
        write_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print_summary(manifest, args.output_dir, reused=False)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
