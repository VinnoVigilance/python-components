"""Select and packet a diverse 4,500-article GPT labeling expansion. No model calls."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from build_labeling_packets import batch_policy
from contracts import POLICY_VERSION, digest, file_digest


SEED = "pdg115-expansion-4500-v1"
SNAPSHOT = Path("research/adverseNews/data/prepared/db-selection-v3")
PILOT = Path("research/adverseNews/data/labeling/final-pilot-500-v2/training-pool-500-title-body-labels.jsonl")
DEFAULT_OUTPUT = Path("research/adverseNews/data/labeling/expansion-4500-v2")
ALLOWED_SPLITS = {"train_candidate", "dev_candidate"}
REFERENCE_SPLIT = "reference_reserved_candidate"


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_rows(path, rows):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def key_hash(value):
    return digest([SEED, value])


def capped_equal_quota(available, target):
    """Water-fill equal source quotas, respecting each publisher's availability."""
    quota = {source: 0 for source in available}
    left = target
    active = set(available)
    while left and active:
        level = max(1, left // len(active))
        filled = [s for s in active if available[s] - quota[s] <= level]
        if filled:
            for source in filled:
                amount = available[source] - quota[source]
                quota[source] += amount
                left -= amount
                active.remove(source)
            continue
        # Everyone can take the same level; distribute any remainder in stable order.
        for source in sorted(active):
            amount = min(level, available[source] - quota[source], left)
            quota[source] += amount
            left -= amount
        if left and active:
            for source in sorted(active):
                if not left:
                    break
                if quota[source] < available[source]:
                    quota[source] += 1
                    left -= 1
    if left:
        raise ValueError(f"source pool is short by {left} rows")
    return quota


def apportion(capacities, total, tie_keys):
    """Proportional integer allocation, with a one-per-stratum coverage floor."""
    capacities = {k: int(v) for k, v in capacities.items() if v > 0}
    if total < 0 or total > sum(capacities.values()):
        raise ValueError("invalid stratum allocation request")
    if not total:
        return {k: 0 for k in capacities}
    allocation = {k: 0 for k in capacities}
    if total >= len(capacities):
        for k in capacities:
            allocation[k] = 1
        total -= len(capacities)
    else:
        for k in sorted(capacities, key=lambda x: (tie_keys[x], str(x)))[:total]:
            allocation[k] = 1
        return allocation
    while total:
        active = [k for k in capacities if allocation[k] < capacities[k]]
        capacity_left = sum(capacities[k] - allocation[k] for k in active)
        if not active or not capacity_left:
            break
        quotas = {k: total * (capacities[k] - allocation[k]) / capacity_left for k in active}
        base = {k: min(capacities[k] - allocation[k], int(quotas[k])) for k in active}
        added = sum(base.values())
        for k, value in base.items():
            allocation[k] += value
        total -= added
        if not total:
            break
        order = sorted((k for k in active if allocation[k] < capacities[k]),
                       key=lambda k: (-(quotas[k] - int(quotas[k])), tie_keys[k], str(k)))
        if not order:
            break
        for k in order:
            if not total:
                break
            allocation[k] += 1
            total -= 1
    if total:
        raise ValueError("could not allocate all records across strata")
    return allocation


def select_by_time(records, count):
    """Stratify by year then month; use stable seeded hashes within each stratum."""
    by_year = defaultdict(list)
    for row in records:
        date = row.get("published_date") or ""
        year = date[:4] if len(date) >= 4 and date[:4].isdigit() else "unknown"
        by_year[year].append(row)
    year_ties = {year: key_hash(["year", year]) for year in by_year}
    year_alloc = apportion({k: len(v) for k, v in by_year.items()}, count, year_ties)
    chosen = []
    for year in sorted(by_year):
        target = year_alloc[year]
        if not target:
            continue
        by_month = defaultdict(list)
        for row in by_year[year]:
            date = row.get("published_date") or ""
            month = date[:7] if len(date) >= 7 and date[4:5] == "-" else "unknown"
            by_month[month].append(row)
        month_ties = {month: key_hash(["month", year, month]) for month in by_month}
        month_alloc = apportion({k: len(v) for k, v in by_month.items()}, target, month_ties)
        for month, rows in by_month.items():
            ordered = sorted(rows, key=lambda r: key_hash(r["article_id"]))
            chosen.extend(ordered[:month_alloc[month]])
    if len(chosen) != count:
        raise ValueError(f"time-stratified sampler selected {len(chosen)} of {count}")
    return chosen


def build(args):
    if args.output.exists():
        raise ValueError(f"output exists; choose a new session directory: {args.output}")
    snapshot = json.loads((args.snapshot / "snapshot.json").read_text(encoding="utf-8"))
    for name, expected in snapshot["outputs_sha256"].items():
        if file_digest(args.snapshot / name) != expected:
            raise ValueError(f"prepared snapshot changed: {name}")
    policy_root = Path(__file__).resolve().parent / "policy" / POLICY_VERSION
    policy_manifest = json.loads((policy_root / "snapshot.json").read_text(encoding="utf-8"))
    for name, expected in policy_manifest["files_sha256"].items():
        if file_digest(policy_root / name) != expected:
            raise ValueError(f"preserved policy changed: {name}")
    prompt = batch_policy((policy_root / "local-teacher-system.txt").read_text(encoding="utf-8"))

    pilot = read_jsonl(args.pilot)
    pilot_hashes = {r["content_hash"] for r in pilot}
    split_rows = read_jsonl(args.snapshot / "provisional-splits.jsonl")
    split_by_article = {r["article_id"]: r for r in split_rows}
    pilot_groups = {split_by_article[r["physical_article_id"]]["group_id"]
                    for r in pilot if r["physical_article_id"] in split_by_article}
    if len(pilot_groups) != len(pilot):
        raise ValueError("could not map every pilot article to one prepared group")

    all_rows = []
    for file in sorted((args.snapshot / "by-dataset").glob("*.jsonl")):
        for row in read_jsonl(file):
            split = split_by_article.get(row["article_id"])
            if split is None:
                raise ValueError(f"missing split metadata for {row['article_id']}")
            if split["candidate_split"] not in ALLOWED_SPLITS:
                continue
            if row["content_hash"] in pilot_hashes or split["group_id"] in pilot_groups:
                continue
            item = dict(row)
            item.update({"group_id": split["group_id"], "candidate_split": split["candidate_split"],
                         "candidate_cv_fold": split["candidate_cv_fold"],
                         "case_review_complete": split["case_review_complete"]})
            all_rows.append(item)

    # One representative for each connected exact/near-duplicate group. When a
    # group crosses sources, assign it to the smaller available source first.
    source_counts = Counter(r["source_name"] for r in all_rows)
    by_group = defaultdict(list)
    for row in all_rows:
        by_group[row["group_id"]].append(row)
    representatives = []
    for group, rows in by_group.items():
        rows.sort(key=lambda r: (source_counts[r["source_name"]],
                                 key_hash(["representative", r["article_id"]])))
        representatives.append(rows[0])
    by_source = defaultdict(list)
    for row in representatives:
        by_source[row["source_name"]].append(row)
    available = {s: len(v) for s, v in by_source.items()}
    quota = capped_equal_quota(available, args.count)

    selected = []
    for source, target in quota.items():
        rows = by_source[source]
        # If every available record is needed, include all; otherwise balance
        # representation across years and months with deterministic seeded picks.
        selected.extend(rows if target == len(rows) else select_by_time(rows, target))
    if len(selected) != args.count or len({r["group_id"] for r in selected}) != args.count:
        raise ValueError("selection is incomplete or contains duplicate automatic groups")
    if len({r["content_hash"] for r in selected}) != args.count:
        raise ValueError("selected records include duplicate content")

    selected.sort(key=lambda r: (r["source_name"], r["dataset_name"],
                                 r.get("published_date") or "", key_hash(r["article_id"])))
    records = [{"article_id": f"AN-P115-R{i:04d}", "title": r["title"], "body": r["body"]}
               for i, r in enumerate(selected, 1)]
    batches, current = [], []
    for record in records:
        proposed = current + [record]
        size = len(prompt + "\n\nARTICLES_JSON\n" +
                    json.dumps(proposed, ensure_ascii=False, indent=2) + "\n")
        if current and (len(proposed) > args.batch_size or size > args.max_characters):
            batches.append(current)
            current = []
        current.append(record)
    if current:
        batches.append(current)

    args.output.mkdir(parents=True)
    packet_dir = args.output / "paste-packets"
    packet_dir.mkdir()
    (args.output / "returns/gpt6-luna").mkdir(parents=True)
    batch_rows, request_to_batch = [], {}
    for number, batch in enumerate(batches, 1):
        batch_id = f"batch-{number:03d}"
        packet = prompt + "\n\nARTICLES_JSON\n" + json.dumps(batch, ensure_ascii=False, indent=2) + "\n"
        path = packet_dir / f"{batch_id}-PASTE-THIS.txt"
        path.write_text(packet, encoding="utf-8", newline="\n")
        batch_rows.append({"batch_id": batch_id,
                           "file": str(path.relative_to(args.output)).replace("\\", "/"),
                           "article_ids": [r["article_id"] for r in batch],
                           "articles": len(batch), "characters": len(packet),
                           "sha256": file_digest(path),
                           "oversize_single_article": len(batch) == 1 and len(packet) > args.max_characters})
        for row in batch:
            request_to_batch[row["article_id"]] = batch_id

    mapping = []
    for request, article in zip(records, selected):
        mapping.append({"request_article_id": request["article_id"],
                        "article_id": article["article_id"], "content_hash": article["content_hash"],
                        "group_id": article["group_id"], "source_name": article["source_name"],
                        "dataset_name": article["dataset_name"], "url": article.get("url", ""),
                        "published_date": article.get("published_date", ""),
                        "language_metadata": article.get("language", ""),
                        "candidate_split_at_preparation": article["candidate_split"],
                        "candidate_cv_fold_at_preparation": article["candidate_cv_fold"],
                        "case_review_complete": article["case_review_complete"],
                        "batch_id": request_to_batch[request["article_id"]],
                        "body_truncated": False})
    write_rows(args.output / "article-map.jsonl", mapping)
    write_rows(args.output / "selected-articles-with-metadata.jsonl", selected)
    write_rows(args.output / "articles-title-body-only.jsonl", records)

    selected_by_source = Counter(r["source_name"] for r in selected)
    selected_by_dataset = Counter(r["dataset_name"] for r in selected)
    selected_by_year = Counter((r.get("published_date") or "unknown")[:4] for r in selected)
    selected_by_split = Counter(r["candidate_split"] for r in selected)
    selected_by_language = Counter(r.get("language") or "<unknown>" for r in selected)
    reference_counts = Counter(r["source_name"] for r in split_rows
                               if r["candidate_split"] == REFERENCE_SPLIT)
    manifest = {
        "schema_version": "adverse-news-expansion-labeling-v1",
        "selection_seed": SEED, "prompt_version": "pdg115-website-batch-v1",
        "policy_version": POLICY_VERSION,
        "policy_sha256": policy_manifest["files_sha256"]["labeling-guide.md"],
        "single_article_prompt_sha256": policy_manifest["files_sha256"]["local-teacher-system.txt"],
        "batch_instructions_sha256": digest(prompt),
        "prepared_snapshot_sha256": file_digest(args.snapshot / "snapshot.json"),
        "pilot_file_sha256": file_digest(args.pilot),
        "selection": "capped equal publisher allocation; all available unique groups from smaller sources; large-source quotas time-stratified by year then month",
        "selected_articles": len(selected), "labeling_batches": batch_rows,
        "selected_by_source": dict(sorted(selected_by_source.items())),
        "selected_by_dataset": dict(sorted(selected_by_dataset.items())),
        "selected_by_year": dict(sorted(selected_by_year.items())),
        "selected_by_provisional_split": dict(sorted(selected_by_split.items())),
        "selected_by_language_metadata": dict(sorted(selected_by_language.items())),
        "reserved_reference_pool_counts_by_source": dict(sorted(reference_counts.items())),
        "reserved_reference_pool_used": False,
        "automatic_groups_unique": len({r["group_id"] for r in selected}),
        "content_hashes_unique": len({r["content_hash"] for r in selected}),
        "body_truncated": False, "model_calls_made": 0,
        "requested_labeler": "GPT-6 Luna, minimal reasoning; actual model/mode must be recorded by labeling session",
        "labels_are": "proposed silver until an independent human reference is established",
        "language_metadata_limit": "Most database language fields are blank; preserve unknown rather than infer from source.",
        "case_review_complete": False,
        "article_map_sha256": file_digest(args.output / "article-map.jsonl"),
        "title_body_file_sha256": file_digest(args.output / "articles-title-body-only.jsonl"),
        "output_contract": "JSONL with exactly article_id and integer label 0/1 per article; labels only, no fallback",
    }
    write_json(args.output / "session.json", manifest)
    print(json.dumps({"selected": len(selected), "unique_groups": len({r["group_id"] for r in selected}),
                      "batches": len(batches), "source_quotas": dict(sorted(selected_by_source.items())),
                      "dataset_coverage": dict(sorted(selected_by_dataset.items())),
                      "provisional_splits": dict(sorted(selected_by_split.items())),
                      "unknown_language_metadata": selected_by_language.get("<unknown>", 0),
                      "max_packet_characters": max(b["characters"] for b in batch_rows),
                      "first_packet": batch_rows[0]["file"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=SNAPSHOT)
    parser.add_argument("--pilot", type=Path, default=PILOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=4500)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-characters", type=int, default=60000)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
