"""Repack remaining unlabeled pilot IDs; preserve policy, IDs and existing answers."""

import argparse
import json
from pathlib import Path

from build_labeling_packets import PROMPT_VERSION, batch_policy, write_json, write_rows
from contracts import digest, file_digest


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-characters", type=int, default=60000)
    args = parser.parse_args()
    if args.output.exists() or args.batch_size < 1 or args.max_characters < 1:
        raise ValueError("use a new output directory and positive size limits")
    manifest = json.loads((args.source / "session.json").read_text(encoding="utf-8"))
    mapping = {r["request_article_id"]: r for r in read_rows(args.source / "article-map.jsonl")}
    records = read_rows(args.source / "articles-title-body-only.jsonl")
    if file_digest(args.source / "article-map.jsonl") != manifest["article_map_sha256"]:
        raise ValueError("source mapping changed")
    if file_digest(args.source / "articles-title-body-only.jsonl") != manifest["title_body_file_sha256"]:
        raise ValueError("source article text changed")
    completed, imports = set(), []
    for path in sorted((args.source / "annotations/deepseek").glob("batch-*.jsonl")):
        imports.append({"file": str(path.resolve()), "sha256": file_digest(path)})
        for row in read_rows(path):
            rid = row["request_article_id"]
            if rid not in mapping or rid in completed:
                raise ValueError("unknown or duplicate completed request ID")
            if row["content_hash"] != mapping[rid]["content_hash"]:
                raise ValueError("completed label text version mismatch")
            if type(row["label"]) is not int or row["label"] not in (0, 1):
                raise ValueError("invalid completed label")
            completed.add(rid)
    remaining = [r for r in records if r["article_id"] not in completed]
    if not remaining:
        raise ValueError("all source articles already labeled by DeepSeek")
    root = Path(__file__).resolve().parent
    policy_dir = root / "policy" / manifest["policy_version"]
    single_path = policy_dir / "local-teacher-system.txt"
    if file_digest(single_path) != manifest["single_article_prompt_sha256"]:
        raise ValueError("base policy changed")
    prompt = batch_policy(single_path.read_text(encoding="utf-8"))
    if digest(prompt) != manifest["batch_instructions_sha256"]:
        raise ValueError("batch instructions changed")
    batches, current = [], []
    for record in remaining:
        if digest([record["title"], record["body"]]) != mapping[record["article_id"]]["content_hash"]:
            raise ValueError("article content hash mismatch")
        proposed = current + [record]
        size = len(prompt + "\n\nARTICLES_JSON\n" + json.dumps(proposed, ensure_ascii=False, indent=2) + "\n")
        if current and (len(proposed) > args.batch_size or size > args.max_characters):
            batches.append(current)
            current = []
        current.append(record)
    if current:
        batches.append(current)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "paste-packets").mkdir()
    (args.output / "returns/deepseek").mkdir(parents=True)
    output_batches, new_map = [], []
    for number, batch in enumerate(batches, 1):
        bid = f"batch-{number:03d}"
        packet = prompt + "\n\nARTICLES_JSON\n" + json.dumps(batch, ensure_ascii=False, indent=2) + "\n"
        relative = f"paste-packets/{bid}-PASTE-THIS.txt"
        path = args.output / relative
        path.write_text(packet, encoding="utf-8", newline="\n")
        output_batches.append({"batch_id": bid, "file": relative,
                               "article_ids": [r["article_id"] for r in batch],
                               "articles": len(batch), "characters": len(packet),
                               "sha256": file_digest(path),
                               "oversize_single_article": len(batch) == 1 and len(packet) > args.max_characters})
        for record in batch:
            original = mapping[record["article_id"]]
            new_map.append({**original, "original_batch_id": original["batch_id"], "batch_id": bid})
    write_rows(args.output / "article-map.jsonl", new_map)
    write_rows(args.output / "articles-title-body-only.jsonl", remaining)
    output_manifest = {
        **manifest, "prompt_version": PROMPT_VERSION,
        "schema_version": "adverse-labeler-comparison-repack-v1",
        "source_session_sha256": file_digest(args.source / "session.json"),
        "repacker_sha256": file_digest(Path(__file__)), "completed_deepseek_imports": imports,
        "completed_request_ids_excluded": sorted(completed),
        "articles": len(remaining), "batches": output_batches,
        "max_articles_per_batch": args.batch_size, "max_packet_characters": args.max_characters,
        "article_map_sha256": file_digest(args.output / "article-map.jsonl"),
        "title_body_file_sha256": file_digest(args.output / "articles-title-body-only.jsonl"),
        "note": "Repack only: original GPT responses and 5-article session remain unchanged; compare by request article ID.",
    }
    # Recalculate pool composition instead of inheriting the 100-article counts.
    from collections import Counter
    output_manifest["articles_by_dataset"] = dict(Counter(r["dataset_name"] for r in new_map))
    write_json(args.output / "session.json", output_manifest)
    print(json.dumps({"completed_excluded": len(completed), "remaining_articles": len(remaining),
                      "batches": len(batches), "batch_sizes": [len(b) for b in batches],
                      "first_batch_ids": output_batches[0]["article_ids"]}, indent=2))


if __name__ == "__main__":
    main()
