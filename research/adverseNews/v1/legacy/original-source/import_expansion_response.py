"""Validate one aggregate teacher response and import it by original packet."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def unique_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError("duplicate JSON key")
        row[key] = value
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--provider", choices=["deepseek", "chatgpt"], required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    manifest_path = args.session / "session.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = {}
    original_lines = {}
    for line in args.input.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line, object_pairs_hook=unique_object)
        if not isinstance(row, dict) or set(row) != {"article_id", "label"}:
            raise ValueError("each row must contain only article_id and label")
        rid, label = row["article_id"], row["label"]
        if not isinstance(rid, str) or rid in rows:
            raise ValueError("article ID is invalid or duplicated")
        if type(label) is not int or label not in (0, 1):
            raise ValueError("label must be integer 0 or 1")
        rows[rid] = label
        original_lines[rid] = line

    expected_by_batch = {batch["batch_id"]: batch["article_ids"] for batch in manifest["batches"]}
    expected = {rid for ids in expected_by_batch.values() for rid in ids}
    if len(expected) != manifest["articles"] or set(rows) != expected:
        raise ValueError(f"response IDs differ from expected set: got {len(rows)}, expected {len(expected)}")

    raw_dir = args.session / "returns" / args.provider
    annotation_dir = args.session / "annotations" / args.provider
    raw_dir.mkdir(parents=True, exist_ok=True)
    annotation_dir.mkdir(parents=True, exist_ok=True)
    for batch_id, ids in expected_by_batch.items():
        raw_path = raw_dir / f"{batch_id}.txt"
        annotation_path = annotation_dir / f"{batch_id}.jsonl"
        if raw_path.exists() or annotation_path.exists():
            raise FileExistsError(f"refusing to overwrite existing {batch_id} response/import")
        raw_path.write_text("\n".join(original_lines[rid] for rid in ids) + "\n", encoding="utf-8", newline="\n")
        importer = Path(__file__).resolve().parent / "import_batch_labels.py"
        subprocess.run([
            sys.executable, str(importer), "--session", str(args.session), "--batch", batch_id,
            "--provider", args.provider, "--model", args.model,
            "--input", str(raw_path), "--output", str(annotation_path),
        ], check=True)

    manifest["deepseek_aggregate_response_sha256" if args.provider == "deepseek" else "chatgpt_aggregate_response_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    manifest["last_aggregate_import"] = {"provider": args.provider, "reported_model_and_mode": args.model,
                                        "articles": len(rows), "source_file": str(args.input.resolve())}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Validated and imported {len(rows)} {args.provider} labels across {len(expected_by_batch)} packets.")


if __name__ == "__main__":
    main()
