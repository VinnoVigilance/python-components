"""Validate saved website labels and preserve proposed silver provenance. No API."""

import argparse
import json
from pathlib import Path

from contracts import digest, file_digest


def unique_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError("duplicate JSON key in response")
        row[key] = value
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--provider", choices=["deepseek", "chatgpt"], required=True)
    parser.add_argument("--model", required=True, help="Model/mode as actually displayed; record unknown revision honestly")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.session / "session.json").read_text(encoding="utf-8"))
    if file_digest(args.session / "article-map.jsonl") != manifest["article_map_sha256"]:
        raise ValueError("article map changed")
    batches = [x for x in manifest["batches"] if x["batch_id"] == args.batch]
    if len(batches) != 1:
        raise ValueError("unknown batch")
    batch = batches[0]
    if file_digest(args.session / batch["file"]) != batch["sha256"]:
        raise ValueError("paste packet changed")
    text = args.input.read_text(encoding="utf-8-sig").strip()
    # Preserve raw file/hash; removing one surrounding code fence is formatting only.
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[-1].strip() != "```":
            raise ValueError("incomplete Markdown fence")
        text = "\n".join(lines[1:-1])
    labels = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line, object_pairs_hook=unique_object)
        if not isinstance(row, dict) or set(row) != {"article_id", "label"}:
            raise ValueError("each response line must contain exactly article_id and label")
        if not isinstance(row["article_id"], str) or row["article_id"] in labels:
            raise ValueError("invalid or duplicate article ID")
        if type(row["label"]) is not int or row["label"] not in (0, 1):
            raise ValueError("label must be integer 0/1; no fallback for invalid output")
        labels[row["article_id"]] = row["label"]
    if set(labels) != set(batch["article_ids"]):
        raise ValueError("response IDs do not exactly match the batch; no labels imported")
    mapping = {x["request_article_id"]: x for line in (args.session / "article-map.jsonl").read_text(encoding="utf-8").splitlines()
               for x in [json.loads(line)]}
    response_hash = file_digest(args.input)
    output = []
    for rid in batch["article_ids"]:
        item = mapping[rid]
        output.append({
            "annotation_id": digest([response_hash, args.provider, args.model, rid]),
            "article_id": item["article_id"], "request_article_id": rid,
            "content_hash": item["content_hash"], "label": labels[rid],
            "origin": "remote_model", "status": "proposed_silver", "human_reference": False,
            "provider": args.provider, "reported_model_and_mode": args.model,
            "batch_id": args.batch, "prompt_version": manifest["prompt_version"],
            "policy_version": manifest["policy_version"], "policy_sha256": manifest["policy_sha256"],
            "packet_sha256": batch["sha256"], "raw_response_sha256": response_hash,
            "raw_response_file": str(args.input.resolve()), "full_body_supplied": True,
            "actual_model_attention_not_verified": True,
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        for row in output:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"Imported {len(output)} proposed silver labels; no human reference or accepted labels created.")


if __name__ == "__main__":
    main()
