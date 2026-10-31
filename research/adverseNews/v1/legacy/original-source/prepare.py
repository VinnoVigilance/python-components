"""Offline data preparation CLI. Does not train, infer or assign adversity labels."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from contracts import (
    Groups, POLICY_VERSION, PREPARATION_VERSION, SPLITS, adapt,
    eligibility, file_digest, text, teacher_label,
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows(path):
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("expected JSON object")
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
                yield line_number, row


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_rows(path, values):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def new_directory(path):
    # Snapshots are never silently overwritten, including after a partial failure.
    path.mkdir(parents=True, exist_ok=False)


def inventory(args):
    config_path = args.config.resolve()
    config = read_json(config_path)
    if config.get("schema_version") != "adverse-inputs-v1":
        raise ValueError("unsupported input configuration")
    adapter = config["adapter"]
    if adapter not in {"canonical", "database"}:
        raise ValueError("adapter must be canonical or database")
    root = (config_path.parent / config["root"]).resolve()
    if adapter == "canonical":
        datasets = config["datasets"]
        if len(set(datasets)) != len(datasets):
            raise ValueError("duplicate dataset in explicit input list")
        paths = [(root / f"{dataset}_final.jsonl", dataset) for dataset in datasets]
    else:
        paths = [(root / name, None) for name in config["files"]]
    if len({p.resolve() for p, _ in paths}) != len(paths):
        raise ValueError("duplicate input file")
    languages = config.get("supported_languages", [])
    if not isinstance(languages, list) or any(not isinstance(x, str) for x in languages):
        raise ValueError("supported_languages must be a list of strings")
    articles, diagnostics, input_files, missing = {}, [], [], []
    source_counts, language_counts, date_counts = Counter(), Counter(), Counter()
    length_counts, reason_counts, warning_counts = Counter(), Counter(), Counter()
    groups, aliases, bodies = Groups(), {}, {}
    total_rows = 0
    for path, dataset in paths:
        if not path.is_file():
            missing.append(str(path))
            continue
        before_hash = file_digest(path)
        count = 0
        # Parse errors are diagnostic records; never manufacture an article/label.
        with path.open(encoding="utf-8-sig") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                count += 1
                location = {"input_file": str(path), "line": line_number}
                try:
                    article = adapt(json.loads(line), adapter, dataset)
                except (ValueError, TypeError, AttributeError) as exc:
                    diagnostics.append({**location, "eligible": False,
                                        "reasons": ["adapter_error"], "detail": str(exc)})
                    reason_counts["adapter_error"] += 1
                    continue
                reasons, warnings = eligibility(article, languages)
                aid = article["article_id"]
                duplicate = aid in articles
                diagnostics.append({**location, "article_id": aid, "eligible": not reasons,
                                    "reasons": reasons, "warnings": warnings,
                                    "repeated_article_version": duplicate})
                reason_counts.update(reasons)
                warning_counts.update(warnings)
                if duplicate:
                    articles[aid]["input_locations"].append(location)
                    continue
                article.update(eligible=not reasons, filter_reasons=reasons,
                               warnings=warnings, input_locations=[location])
                articles[aid] = article
                if reasons:
                    continue
                groups.find(aid)
                for index, key in ((aliases, article["logical_id"]),
                                   (bodies, article["body_group_hash"])):
                    if key in index:
                        groups.join(aid, index[key])
                    index[key] = aid
                source_counts[article["dataset_name"]] += 1
                language_counts[article["language"] or "<unknown>"] += 1
                date_counts[(article["published_at"] or "<unknown>")[:7]] += 1
                size = len(article["body"])
                length_counts["<500" if size < 500 else "500-1999" if size < 2000
                              else "2000-7999" if size < 8000 else ">=8000"] += 1
        if file_digest(path) != before_hash:
            raise ValueError(f"input changed during inventory: {path}")
        input_files.append({"path": str(path), "sha256": before_hash, "rows": count})
        total_rows += count
    if not input_files:
        raise ValueError("no configured input files found")
    eligible = sorted((x for x in articles.values() if x["eligible"]), key=lambda x: x["article_id"])
    group_rows = [{"article_id": x["article_id"], "auto_group_id": groups.find(x["article_id"]),
                   "case_group_ids": [], "synthetic_parent_ids": [], "split": None,
                   "sampling_component": None, "selection_reason": None,
                   "reviewed_by": None, "reviewed_at": None,
                   "case_review_complete": False, "quality_review_complete": False,
                   "language_review_complete": False} for x in eligible]
    new_directory(args.output)
    write_rows(args.output / "articles.jsonl", sorted(articles.values(), key=lambda x: x["article_id"]))
    write_rows(args.output / "diagnostics.jsonl", diagnostics)
    write_rows(args.output / "groups-to-review.jsonl", group_rows)
    outputs = {name: file_digest(args.output / name) for name in
               ("articles.jsonl", "diagnostics.jsonl", "groups-to-review.jsonl")}
    manifest = {
        "schema_version": "adverse-inventory-v1", "preparation_version": PREPARATION_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(), "adapter": adapter,
        "config_sha256": file_digest(config_path), "input_files": input_files,
        "missing_files": missing, "output_sha256": outputs,
        "counts": {"input_rows": total_rows, "unique_article_versions": len(articles),
                   "eligible_versions": len(eligible), "automatic_groups": len({groups.find(x["article_id"]) for x in eligible}),
                   "adapter_errors": reason_counts["adapter_error"],
                   "filtered_versions": sum(not x["eligible"] for x in articles.values()),
                   "repeated_version_rows": sum(x.get("repeated_article_version", False) for x in diagnostics)},
        "eligible_by_dataset": dict(source_counts), "eligible_by_language": dict(language_counts),
        "eligible_by_publication_month": dict(sorted(date_counts.items())),
        "eligible_by_body_characters": dict(length_counts),
        "filter_reason_occurrences": dict(reason_counts), "warning_occurrences": dict(warning_counts),
        "limitations": ["Not labeled; no prevalence estimate", "No automatic near-duplicate or case detection",
                         "Language metadata is not language identification", "Eligibility still requires extraction review"],
    }
    write_json(args.output / "inventory.json", manifest)
    print(json.dumps(manifest["counts"], indent=2))


def snapshot(path):
    manifest = read_json(path / "inventory.json")
    if manifest.get("schema_version") != "adverse-inventory-v1":
        raise ValueError("unsupported inventory")
    for name, expected in manifest["output_sha256"].items():
        if file_digest(path / name) != expected:
            raise ValueError(f"snapshot changed: {name}")
    articles = {x["article_id"]: x for _, x in rows(path / "articles.jsonl")}
    return manifest, articles


def reserve(args):
    _, articles = snapshot(args.inventory)
    eligible = {aid: x for aid, x in articles.items() if x["eligible"]}
    original = {x["article_id"]: x for _, x in rows(args.inventory / "groups-to-review.jsonl")}
    reviewed = {}
    groups, links = Groups(), {}
    for _, row in rows(args.groups):
        aid = row["article_id"]
        if aid not in eligible or aid in reviewed:
            raise ValueError("reviewed groups must contain each eligible article exactly once")
        if row.get("auto_group_id") != original[aid]["auto_group_id"]:
            raise ValueError("automatic group was changed")
        for key in ("case_review_complete", "quality_review_complete", "language_review_complete"):
            if row.get(key) is not True:
                raise ValueError(f"{aid}: {key} must be true before reserving splits")
        for key in ("reviewed_by", "reviewed_at", "sampling_component", "selection_reason"):
            text(row.get(key), key)
        if row.get("split") not in SPLITS:
            raise ValueError(f"split must be one of {sorted(SPLITS)}")
        if row["sampling_component"] not in {"representative", "boundary", "targeted", "challenge"}:
            raise ValueError("unsupported sampling component")
        if row["split"] == "final" and row["sampling_component"] != "representative":
            raise ValueError("final is representative; reserve enriched cases as challenge")
        groups.find(aid)
        for key in ("case_group_ids", "synthetic_parent_ids"):
            if not isinstance(row.get(key), list):
                raise ValueError(f"{key} must be a list")
            for item in row[key]:
                text(item, key)
        for key in ["auto:" + row["auto_group_id"]] + ["case:" + c for c in row["case_group_ids"]]:
            if key in links:
                groups.join(aid, links[key])
            links[key] = aid
        reviewed[aid] = row
    if set(reviewed) != set(eligible):
        raise ValueError("reviewed groups do not cover the entire eligible snapshot")
    for aid, row in reviewed.items():
        for parent in row["synthetic_parent_ids"]:
            if parent not in reviewed:
                raise ValueError("synthetic parent absent from snapshot")
            groups.join(aid, parent)
    assignments = {}
    for aid, row in reviewed.items():
        gid = groups.find(aid)
        if gid in assignments and assignments[gid] != row["split"]:
            raise ValueError(f"related articles cross splits in group {gid}")
        assignments[gid] = row["split"]
    output = [{**reviewed[aid], "group_id": groups.find(aid),
               "content_hash": eligible[aid]["content_hash"]} for aid in sorted(reviewed)]
    new_directory(args.output)
    write_rows(args.output / "splits.jsonl", output)
    write_json(args.output / "reservation.json", {
        "schema_version": "adverse-reservation-v1",
        "inventory_sha256": file_digest(args.inventory / "inventory.json"),
        "reviewed_groups_sha256": file_digest(args.groups),
        "splits_sha256": file_digest(args.output / "splits.jsonl"),
        "article_counts": dict(Counter(x["split"] for x in output)),
        "group_counts": dict(Counter(assignments.values())),
        "note": "Explicit reviewed assignments; no automatic random split or prevalence claim.",
    })
    print(f"Reserved {len(output)} article versions in {len(assignments)} groups.")


def validate_annotations(args):
    _, articles = snapshot(args.inventory)
    policy = read_json(args.policy / "snapshot.json")
    if policy["policy_version"] != POLICY_VERSION:
        raise ValueError("unsupported policy version")
    for name, expected in policy["files_sha256"].items():
        if file_digest(args.policy / name) != expected:
            raise ValueError(f"policy snapshot changed: {name}")
    events = {}
    accepted = {}
    for _, event in rows(args.annotations):
        eid = text(event.get("annotation_id"), "annotation_id")
        aid = event.get("article_id")
        article = articles.get(aid)
        if eid in events or not article or not article["eligible"]:
            raise ValueError("duplicate annotation ID, unknown article, or filtered article")
        if type(event.get("label")) is not int or event["label"] not in (0, 1):
            raise ValueError("label must be integer 0/1 (booleans are forbidden)")
        if event.get("content_hash") != article["content_hash"]:
            raise ValueError("annotation is for a different text version")
        if event.get("policy_version") != policy["policy_version"] or event.get("policy_sha256") != policy["files_sha256"]["labeling-guide.md"]:
            raise ValueError("annotation policy version/hash mismatch")
        for key in ("annotator_id", "created_at"):
            text(event.get(key), key)
        if event.get("origin") not in {"human", "local_teacher"}:
            raise ValueError("origin must be human or local_teacher")
        if event["origin"] == "human":
            for key in ("rationale", "reason_code"):
                text(event.get(key), key)
        if event.get("status") not in {"proposed", "accepted"}:
            raise ValueError("status must be proposed or accepted")
        if type(event.get("independent_of_teacher")) is not bool:
            raise ValueError("independent_of_teacher must be explicit boolean")
        if event.get("reference") not in {"none", "independent_human"}:
            raise ValueError("reference must be none or independent_human")
        evidence = event.get("evidence")
        if not isinstance(evidence, list) or (event["origin"] == "human" and event["label"] == 1 and not evidence):
            raise ValueError("evidence list required; human positive labels need supporting passages")
        for quote in evidence:
            text(quote, "evidence passage")
            if quote not in article["body"] and quote not in article["title"]:
                raise ValueError("evidence must be an exact title/body passage")
        if type(event.get("resolution_update")) is not bool:
            raise ValueError("resolution_update must be explicit boolean")
        if event.get("reason_code") == "RESOLUTION_ONLY" and (event["label"] != 0 or not event["resolution_update"]):
            raise ValueError("resolution-only requires label 0 and retained resolution context")
        if event["resolution_update"]:
            text(event.get("resolution_context"), "resolution_context")
        review_ids = event.get("review_of")
        if not isinstance(review_ids, list) or any(not isinstance(x, str) for x in review_ids):
            raise ValueError("review_of must be a list of prior annotation IDs")
        parents = []
        for prior_id in review_ids:
            prior = events.get(prior_id)
            if not prior or prior["article_id"] != aid:
                raise ValueError("review_of must name earlier annotations for the same article")
            parents.append(prior)
        if event["origin"] == "local_teacher":
            if event["status"] != "proposed" or event["reference"] != "none" or event["independent_of_teacher"]:
                raise ValueError("teacher suggestions remain proposed silver, never human reference")
            teacher = event.get("teacher", {})
            if teacher_label(text(teacher.get("raw_response"), "teacher.raw_response")) != event["label"]:
                raise ValueError("stored label differs from original teacher response")
            for key in ("model", "revision", "quantization", "runtime", "runtime_version",
                        "prompt_sha256", "request_id", "response_sha256", "coverage_recipe"):
                text(teacher.get(key), f"teacher.{key}")
            if teacher["prompt_sha256"] != policy["files_sha256"]["local-teacher-system.txt"]:
                raise ValueError("teacher prompt hash mismatch")
            if teacher.get("full_body_covered") is not True or not isinstance(teacher.get("generation_settings"), dict):
                raise ValueError("record full coverage and actual generation settings")
        if event["reference"] == "independent_human":
            if event["origin"] != "human" or not event["independent_of_teacher"] or event["status"] != "accepted":
                raise ValueError("reference requires accepted independent human review")
            independent = [p for p in parents if p["origin"] == "human" and p["independent_of_teacher"]
                           and p["annotator_id"] != event["annotator_id"]]
            if not independent or any(p["origin"] != "human" or not p["independent_of_teacher"] for p in parents):
                raise ValueError("reference needs a different independent human judgment, no teacher parents")
        if event["status"] == "accepted":
            # Corrections supersede an accepted event explicitly; preserve both rows.
            previous = accepted.get(aid)
            if previous and previous not in review_ids:
                raise ValueError("a correction must review the previous accepted annotation")
            accepted[aid] = eid
        events[eid] = event
    print(json.dumps({"events": len(events), "accepted_articles": len(accepted),
                      "independent_reference_articles": sum(events[eid]["reference"] == "independent_human" for eid in accepted.values()),
                      "note": "Structural provenance checks do not verify human independence or semantic correctness."}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="Inventory explicit JSONL files; never assign labels")
    inv.add_argument("--config", type=Path, required=True)
    inv.add_argument("--output", type=Path, required=True)
    inv.set_defaults(action=inventory)
    res = sub.add_parser("reserve", help="Validate reviewed groups and freeze explicit splits")
    res.add_argument("--inventory", type=Path, required=True)
    res.add_argument("--groups", type=Path, required=True)
    res.add_argument("--output", type=Path, required=True)
    res.set_defaults(action=reserve)
    ann = sub.add_parser("validate-annotations", help="Check append-only binary provenance records")
    ann.add_argument("--inventory", type=Path, required=True)
    ann.add_argument("--annotations", type=Path, required=True)
    ann.add_argument("--policy", type=Path, default=Path(__file__).parent / "policy" / POLICY_VERSION)
    ann.set_defaults(action=validate_annotations)
    args = parser.parse_args()
    try:
        args.action(args)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        parser.exit(2, f"Preparation failed: {exc}\n")


if __name__ == "__main__":
    main()
