"""Build a disjoint paired-teacher labeling set from prepared training candidates."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from contracts import POLICY_VERSION, digest, file_digest
from build_labeling_packets import PROMPT_VERSION, batch_policy, write_json, write_rows


SEED = "pdg115-paired-expansion-v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=400)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-characters", type=int, default=60000)
    args = parser.parse_args()
    if args.output.exists() or min(args.count, args.batch_size, args.max_characters) < 1:
        raise ValueError("choose a new output directory and positive limits")

    snapshot = json.loads((args.snapshot / "snapshot.json").read_text(encoding="utf-8"))
    for name, expected in snapshot["outputs_sha256"].items():
        if file_digest(args.snapshot / name) != expected:
            raise ValueError(f"prepared snapshot changed: {name}")
    pilot_manifest = json.loads((args.pilot / "session.json").read_text(encoding="utf-8"))
    if file_digest(args.pilot / "article-map.jsonl") != pilot_manifest["article_map_sha256"]:
        raise ValueError("pilot mapping changed")
    excluded_groups = {r["group_id"] for r in
                       (json.loads(line) for line in (args.pilot / "article-map.jsonl").read_text(encoding="utf-8").splitlines())}

    root = Path(__file__).resolve().parent
    policy_dir = root / "policy" / POLICY_VERSION
    policy_snapshot = json.loads((policy_dir / "snapshot.json").read_text(encoding="utf-8"))
    for name, expected in policy_snapshot["files_sha256"].items():
        if file_digest(policy_dir / name) != expected:
            raise ValueError(f"preserved policy changed: {name}")
    prompt = batch_policy((policy_dir / "local-teacher-system.txt").read_text(encoding="utf-8"))

    pools = defaultdict(list)
    with (args.snapshot / "working-candidates.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            article = json.loads(line)
            if article["candidate_split"] == "train_candidate" and article["group_id"] not in excluded_groups:
                pools[article["dataset_name"]].append(article)
    for dataset in pools:
        pools[dataset].sort(key=lambda row: digest([SEED, row["article_id"]]))

    selected, seen = [], set(excluded_groups)
    while len(selected) < args.count:
        before = len(selected)
        for dataset in sorted(pools):
            while pools[dataset] and pools[dataset][0]["group_id"] in seen:
                pools[dataset].pop(0)
            if pools[dataset] and len(selected) < args.count:
                row = pools[dataset].pop(0)
                seen.add(row["group_id"])
                selected.append(row)
        if before == len(selected):
            break
    if len(selected) != args.count:
        raise ValueError(f"only {len(selected)} disjoint training candidates available")

    records = [{"article_id": f"AN-P115-X{i:03d}", "title": row["title"], "body": row["body"]}
               for i, row in enumerate(selected, 1)]
    batches, current = [], []
    for record in records:
        proposed = current + [record]
        packet_chars = len(prompt + "\n\nARTICLES_JSON\n" + json.dumps(proposed, ensure_ascii=False, indent=2) + "\n")
        if current and (len(proposed) > args.batch_size or packet_chars > args.max_characters):
            batches.append(current)
            current = []
        current.append(record)
    if current:
        batches.append(current)

    args.output.mkdir(parents=True, exist_ok=False)
    packet_dir = args.output / "paste-packets"
    packet_dir.mkdir()
    batch_rows, assignment = [], {}
    for n, batch in enumerate(batches, 1):
        batch_id = f"batch-{n:03d}"
        text = prompt + "\n\nARTICLES_JSON\n" + json.dumps(batch, ensure_ascii=False, indent=2) + "\n"
        path = packet_dir / f"{batch_id}-PASTE-THIS.txt"
        path.write_text(text, encoding="utf-8", newline="\n")
        batch_rows.append({"batch_id": batch_id, "file": f"paste-packets/{path.name}",
                           "article_ids": [r["article_id"] for r in batch], "articles": len(batch),
                           "characters": len(text), "sha256": file_digest(path)})
        for row in batch:
            assignment[row["article_id"]] = batch_id

    mapping = [{"request_article_id": record["article_id"], "article_id": article["article_id"],
                "content_hash": article["content_hash"], "group_id": article["group_id"],
                "dataset_name": article["dataset_name"], "source_name": article["source_name"],
                "batch_id": assignment[record["article_id"]], "candidate_split": article["candidate_split"],
                "full_body_characters": len(article["body"]), "body_truncated": False}
               for record, article in zip(records, selected)]
    write_rows(args.output / "article-map.jsonl", mapping)
    write_rows(args.output / "articles-title-body-only.jsonl", records)
    manifest = {
        "schema_version": "adverse-paired-expansion-v1", "seed": SEED,
        "prompt_version": PROMPT_VERSION,
        "policy_version": POLICY_VERSION,
        "policy_sha256": policy_snapshot["files_sha256"]["labeling-guide.md"],
        "single_article_prompt_sha256": policy_snapshot["files_sha256"]["local-teacher-system.txt"],
        "batch_instructions_sha256": digest(prompt),
        "prepared_snapshot_sha256": file_digest(args.snapshot / "snapshot.json"),
        "builder_sha256": file_digest(Path(__file__)),
        "pilot_session_sha256": file_digest(args.pilot / "session.json"),
        "excluded_pilot_groups": len(excluded_groups),
        "selection": "dataset round-robin, seeded within dataset, train_candidate only, one automatic group per article",
        "not_representative_prevalence_sample": True, "case_review_pending": True,
        "model_calls_made": 0, "articles": len(records), "batches": batch_rows,
        "articles_by_dataset": dict(Counter(r["dataset_name"] for r in selected)),
        "article_map_sha256": file_digest(args.output / "article-map.jsonl"),
        "title_body_file_sha256": file_digest(args.output / "articles-title-body-only.jsonl"),
        "labels_are": "proposed silver until reviewed; model agreement is not human truth",
        "both_labelers_use_identical_packets": True,
    }
    write_json(args.output / "session.json", manifest)
    print(json.dumps({"articles": len(records), "batches": len(batches),
                      "batch_sizes": [len(batch) for batch in batches],
                      "articles_by_dataset": manifest["articles_by_dataset"],
                      "excluded_pilot_groups": len(excluded_groups),
                      "max_packet_characters": max(row["characters"] for row in batch_rows)}, indent=2))


if __name__ == "__main__":
    main()
