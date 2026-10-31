"""Prepare supplied database CSV exports. No labels, models or training.

This is a data preparation job, not an implementation test. CSV exports remain
untouched. Outputs require a new directory so a selection is never overwritten.
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re

from contracts import Groups, digest, eligibility, file_digest, normalized_body


VERSION = "adverse-db-selection-v3"
SEED = "pdg115-preparation-v1"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_rows(path, values):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in values:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def date_value(value):
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def object_rows(value):
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return []


def recover_metadata(row):
    """SQL NULL was exported as literal NULL. Recover array-valued metadata.

    Keep the original CSV untouched. Only metadata/text CSV null sentinels are
    decoded, not text embedded in an otherwise valid article.
    """
    row = {key: "" if value == "NULL" else value for key, value in row.items()}
    recovered = []
    if row.get("canonical_payload"):
        payload = json.loads(row["canonical_payload"])
        if not isinstance(payload, dict):
            raise ValueError("canonical_payload must be a JSON object")
        content = payload.get("Content") or {}
        for key, canonical_key in (("title", "Title"), ("body", "BodyText")):
            if (content.get(canonical_key) or "") != row[key]:
                raise ValueError(f"CSV {key} differs from canonical payload")
        sources = object_rows(payload.get("Sources"))
        urls = object_rows(payload.get("URLs"))
        dates = object_rows(payload.get("Dates"))
        published = [x for x in dates if str(x.get("Type", "")).casefold() == "published"]
        metadata = payload.get("Metadata") or payload.get("Taxonomy") or {}
        candidates = {
            "url": next((x.get("SourceURL") for x in sources if x.get("SourceURL")), "")
                   or next((x.get("URL") for x in urls if str(x.get("URLType", "")).casefold() == "article"), ""),
            "published_at": next((x.get("FullDate") for x in published if x.get("FullDate")), ""),
            "language": metadata.get("Language", ""),
        }
        for key, value in candidates.items():
            if not row[key] and isinstance(value, str) and value.strip() and value != "NULL":
                row[key] = value
                recovered.append(key)
    return row, recovered


def prepare(args):
    csv.field_size_limit(100_000_000)
    if args.output.exists():
        raise ValueError("output already exists; use a new snapshot directory")
    if args.per_dataset < 1:
        raise ValueError("per-dataset working-selection cap must be positive")
    inputs = [path.resolve() for path in args.inputs]
    if len(set(inputs)) != len(inputs):
        raise ValueError("duplicate input paths")
    metadata_rows, metadata_input, metadata_seen = {}, None, set()
    if args.metadata:
        before = file_digest(args.metadata)
        with args.metadata.open(encoding="utf-8-sig", newline="") as stream:
            for raw_meta in csv.DictReader(stream, strict=True):
                meta = {key: "" if value == "NULL" else value for key, value in raw_meta.items()}
                if meta["record_found"].casefold() not in {"true", "t", "1"}:
                    raise ValueError("metadata export contains an unavailable physical record")
                key = meta["media_record_id"]
                if key in metadata_rows:
                    raise ValueError("duplicate metadata ID")
                metadata_rows[key] = meta
        if file_digest(args.metadata) != before:
            raise ValueError("metadata input changed while reading")
        metadata_input = {"filename": args.metadata.name, "sha256": before,
                          "bytes": args.metadata.stat().st_size, "rows": len(metadata_rows)}
    articles, diagnostics, files = {}, [], []
    counts, eligible_counts = Counter(), Counter()
    warnings, filtered = Counter(), Counter()
    languages, months, lengths = Counter(), Counter(), Counter()
    recovered_metadata = Counter()
    groups, links = Groups(), {}
    raw_rows = 0
    for path in inputs:
        before = file_digest(path)
        file_count = 0
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            required = {"media_record_id", "vv_media_id", "version_no", "source_name",
                        "dataset_name", "title", "body", "language", "published_at", "url", "record_key"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError(f"missing required columns: {path}")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError(f"duplicate CSV column names: {path}")
            for row_number, row in enumerate(reader, 1):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"CSV column mismatch: {path}, record {row_number}")
                row, recovered = recover_metadata(row)
                metadata_fields = []
                meta = metadata_rows.get(row["media_record_id"])
                if meta:
                    for key in ("vv_media_id", "version_no", "source_name", "dataset_name", "database_content_hash"):
                        if row.get(key, "") != meta.get(key, ""):
                            raise ValueError(f"metadata version/identity mismatch: {row['media_record_id']}, {key}")
                    for key in ("url", "published_at", "language", "published_original"):
                        if row.get(key) and meta.get(key) and row[key] != meta[key]:
                            raise ValueError(f"conflicting metadata: {row['media_record_id']}, {key}")
                        if not row.get(key) and meta.get(key):
                            row[key] = meta[key]
                            metadata_fields.append(key)
                    metadata_seen.add(row["media_record_id"])
                elif args.metadata and row["dataset_name"] == "UK_GOV_NEWS_COMMUNICATIONS":
                    raise ValueError("UK article missing from exact-ID metadata export")
                recovered_metadata.update(recovered)
                file_count += 1
                raw_rows += 1
                for key in ("media_record_id", "vv_media_id", "version_no", "source_name", "dataset_name"):
                    if not row[key].strip():
                        raise ValueError(f"missing {key}: {path}, record {row_number}")
                aid = "db:" + row["media_record_id"]
                if aid in articles:
                    raise ValueError(f"repeated physical database ID: {aid}; inspect exports")
                source, dataset = row["source_name"], row["dataset_name"]
                if not re.fullmatch(r"[A-Za-z0-9_-]+", dataset):
                    raise ValueError("unsafe dataset filename")
                content_hash = digest([row["title"], row["body"]])
                body_hash = digest(normalized_body(row["body"])) if row["body"].strip() else None
                published_date = date_value(row["published_at"])
                article = {
                    "article_id": aid, "media_record_id": row["media_record_id"],
                    "vv_media_id": row["vv_media_id"], "version_no": row["version_no"],
                    "source_name": source, "dataset_name": dataset,
                    "external_id": row.get("external_id", ""), "record_key": row.get("record_key", ""),
                    "url": row["url"], "title": row["title"], "body": row["body"],
                    "language": row["language"], "published_at": row["published_at"],
                    "published_original": row.get("published_original", ""),
                    "published_date": published_date, "created_at": row.get("created_at", ""),
                    "valid_from": row.get("valid_from", ""),
                    "database_content_hash": row.get("database_content_hash", ""),
                    "pipeline_version": row.get("pipeline_version", ""),
                    "content_hash": content_hash, "body_group_hash": body_hash,
                    "preparation_version": VERSION,
                    "sampling_method": row.get("sampling_method", "all_exported_current_non_uk"),
                    "input_file": path.name, "input_record_number": row_number,
                    "metadata_recovered_from_payload": recovered,
                    "metadata_overlay_fields": metadata_fields,
                }
                reasons, notices = eligibility(article, [])
                if row["published_at"] and not published_date:
                    notices.append("publication_date_not_iso")
                if published_date and published_date > args.as_of:
                    notices.append("publication_date_after_snapshot_date")
                article.update(eligible=not reasons, filter_reasons=reasons, warnings=notices)
                articles[aid] = article
                counts[dataset] += 1
                filtered.update(reasons)
                warnings.update(notices)
                diagnostics.append({"article_id": aid, "input_file": path.name,
                                    "input_record_number": row_number, "eligible": not reasons,
                                    "reasons": reasons, "warnings": notices})
                if reasons:
                    continue
                eligible_counts[dataset] += 1
                languages[row["language"] or "<unknown>"] += 1
                months[published_date[:7] if published_date else "<unknown>"] += 1
                size = len(row["body"])
                lengths["<500" if size < 500 else "500-1999" if size < 2000
                        else "2000-7999" if size < 8000 else ">=8000"] += 1
                groups.find(aid)
                # Transitive grouping across physical versions, duplicate full bodies
                # and exact URLs. No semantic or same-case claim is made here.
                keys = ["logical:" + row["vv_media_id"], "body:" + body_hash]
                if row["record_key"].strip():
                    keys.append("record:" + digest([source, dataset, row["record_key"]]))
                if row["url"].strip():
                    keys.append("url:" + row["url"].strip())
                for key in keys:
                    if key in links:
                        groups.join(aid, links[key])
                    links[key] = aid
        if file_digest(path) != before:
            raise ValueError(f"input changed while reading: {path}")
        files.append({"filename": path.name, "bytes": path.stat().st_size,
                      "sha256": before, "rows": file_count})

    if metadata_seen != set(metadata_rows):
        raise ValueError("metadata contains IDs absent from the supplied article exports")

    eligible = [row for row in articles.values() if row["eligible"]]
    components = defaultdict(list)
    for article in eligible:
        components[groups.find(article["article_id"])].append(article["article_id"])
    group_ids = {root: digest(sorted(members)) for root, members in components.items()}
    assignments = {}
    by_dataset = defaultdict(list)
    for article in eligible:
        aid = article["article_id"]
        gid = group_ids[groups.find(aid)]
        bucket = int(digest([SEED, "reservation", gid])[:16], 16) % 100
        split = "train_candidate" if bucket < 70 else "dev_candidate" if bucket < 85 else "reference_reserved_candidate"
        fold = int(digest([SEED, "cv", gid])[:16], 16) % 5 if split == "train_candidate" else None
        assignment = {
            "article_id": aid, "group_id": gid, "source_name": article["source_name"],
            "dataset_name": article["dataset_name"], "content_hash": article["content_hash"],
            "candidate_split": split, "candidate_cv_fold": fold,
            "published_date": article["published_date"], "status": "provisional_case_review_pending",
            "case_review_complete": False,
        }
        assignments[aid] = assignment
        article.update(group_id=gid, candidate_split=split, candidate_cv_fold=fold)
        by_dataset[article["dataset_name"]].append(article)

    # Working pool: one representative per normalized body, cap each dataset.
    # Remaining candidates stay available in the complete per-dataset files.
    representatives = {}
    for article in sorted(eligible, key=lambda x: digest([SEED, "representative", x["article_id"]])):
        representatives.setdefault(article["body_group_hash"], article)
    choices = defaultdict(list)
    for article in representatives.values():
        choices[article["dataset_name"]].append(article)
    selected = []
    for dataset in sorted(choices):
        candidates = sorted(choices[dataset], key=lambda x: digest([SEED, "working", x["article_id"]]))
        selected.extend(candidates[:args.per_dataset])
    selected_ids = {x["article_id"] for x in selected}
    selected.sort(key=lambda x: (x["dataset_name"], x["article_id"]))
    group_rows = []
    for root, members in sorted(components.items()):
        group_rows.append({"group_id": group_ids[root], "article_ids": sorted(members),
                           "sources": sorted({articles[aid]["source_name"] for aid in members}),
                           "grouping": "connected_logical_id_record_key_exact_url_or_normalized_full_body",
                           "case_review_complete": False})

    # Write only after input parsing succeeds. Raw files are never modified.
    args.output.mkdir(parents=True, exist_ok=False)
    dataset_dir = args.output / "by-dataset"
    dataset_dir.mkdir()
    for dataset, values in sorted(by_dataset.items()):
        write_rows(dataset_dir / f"{dataset}.jsonl", sorted(values, key=lambda x: x["article_id"]))
    write_rows(args.output / "working-candidates.jsonl", selected)
    write_rows(args.output / "diagnostics.jsonl", diagnostics)
    write_rows(args.output / "automatic-groups.jsonl", group_rows)
    write_rows(args.output / "provisional-splits.jsonl", [
        {**assignments[aid], "in_working_selection": aid in selected_ids} for aid in sorted(assignments)
    ])
    write_rows(args.output / "filtered-records.jsonl", [
        {key: value for key, value in row.items() if key not in {"title", "body"}}
        for row in articles.values() if not row["eligible"]
    ])
    # Small read-only preview for discussing annotation effort, not a benchmark.
    preview = []
    for dataset in sorted(choices):
        candidates = [x for x in selected if x["dataset_name"] == dataset
                      and x["candidate_split"] == "train_candidate"]
        if candidates:
            preview.append(min(candidates, key=lambda x: digest([SEED, "preview", x["article_id"]])))
    write_rows(args.output / "preview-one-per-dataset.jsonl", preview)
    summary = {
        "schema_version": VERSION, "seed": SEED, "as_of": args.as_of,
        "created_at": datetime.now(timezone.utc).isoformat(), "inputs": files,
        "metadata_input": metadata_input,
        "counts": {"raw_rows": raw_rows, "eligible_articles": len(eligible),
                   "filtered_articles": len(articles) - len(eligible),
                   "automatic_groups": len(components),
                   "groups_with_multiple_articles": sum(len(x) > 1 for x in components.values()),
                   "distinct_normalized_bodies": len(representatives),
                   "working_articles": len(selected), "preview_articles": len(preview)},
        "raw_by_dataset": dict(sorted(counts.items())),
        "eligible_by_dataset": dict(sorted(eligible_counts.items())),
        "working_by_dataset": dict(sorted(Counter(x["dataset_name"] for x in selected).items())),
        "eligible_by_language_metadata": dict(languages), "eligible_by_month": dict(sorted(months.items())),
        "eligible_by_body_characters": dict(lengths), "filter_reasons": dict(filtered),
        "warning_occurrences": dict(warnings),
        "metadata_recovered_from_payload": dict(recovered_metadata),
        "candidate_split_counts": dict(Counter(x["candidate_split"] for x in eligible)),
        "working_candidate_split_counts": dict(Counter(x["candidate_split"] for x in selected)),
        "source_names": sorted({x["source_name"] for x in eligible}),
        "working_cap_per_dataset": args.per_dataset,
        "limitations": [
            "No labels, training, inference, tests or prevalence estimates",
            "Conservative eligibility is not a completed extraction/language review",
            "No automatic near-duplicate, syndicated paraphrase or same-case detection",
            "Split/fold suggestions are provisional, not a case-safe frozen final evaluation",
            "Working selection caps sources and is not representative of database traffic",
            "UK input is already a 5000-row sample; database source/date proportions differ",
            "The small preview is exploratory and must not be reported as evaluation",
            "CSV exact NULL values decoded as SQL null sentinels; raw exports retained",
            "Language remains unknown where absent in the original payload and metadata export",
        ],
    }
    write_json(args.output / "inventory.json", summary)
    write_json(args.output / "snapshot.json", {
        "schema_version": VERSION, "script_sha256": file_digest(Path(__file__)),
        "contracts_sha256": file_digest(Path(__file__).with_name("contracts.py")),
        "outputs_sha256": {str(path.relative_to(args.output)).replace("\\", "/"): file_digest(path)
                           for path in sorted(args.output.rglob("*")) if path.is_file()},
    })
    print(json.dumps({"counts": summary["counts"], "eligible_by_dataset": summary["eligible_by_dataset"],
                      "working_by_dataset": summary["working_by_dataset"], "filter_reasons": dict(filtered),
                      "warning_occurrences": dict(warnings), "languages": dict(languages),
                      "candidate_split_counts": summary["candidate_split_counts"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, help="Exact physical-ID metadata overlay; never resample")
    parser.add_argument("--per-dataset", type=int, default=2000)
    parser.add_argument("--as-of", default="2026-10-28", help="Snapshot date in user's timezone, YYYY-MM-DD")
    args = parser.parse_args()
    date.fromisoformat(args.as_of)
    prepare(args)


if __name__ == "__main__":
    main()
