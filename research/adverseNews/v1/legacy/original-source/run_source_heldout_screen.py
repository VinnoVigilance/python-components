"""Hold-one-publisher-out robustness screen for the fixed classical candidates."""

import argparse
import csv
import json
import platform
import time
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import scipy
import sklearn
from sklearn.model_selection import LeaveOneGroupOut

from contracts import file_digest
from run_classical_screen import (DEFAULT_DATA, SEED, candidate_specs, get_scores,
                                  make_model, metrics, read_jsonl, vectorize_fold)


def write_csv(path, rows):
    columns = list(rows[0]) if rows else ["model"]
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path,
                        default=Path("research/adverseNews/results/source-heldout-screen-v1"))
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output directory exists; choose a new run path")
    rows = read_jsonl(args.data)
    y = np.asarray([r["label"] for r in rows], dtype=int)
    texts = [(r["title"] + "\n\n" + r["body"]).strip() for r in rows]
    sources = np.asarray([r["source_name"] for r in rows])
    groups = np.asarray([r["automatic_group_id"] for r in rows])
    if len(rows) != 500 or len(set(groups)) != len(rows):
        raise ValueError("expected 500 rows with unique automatic groups")

    splitter = LeaveOneGroupOut()
    split_list = list(splitter.split(np.zeros(len(y)), y, sources))
    fold_source = {}
    for fold, (train, valid) in enumerate(split_list, 1):
        heldout_sources = set(sources[valid])
        if len(heldout_sources) != 1 or heldout_sources & set(sources[train]):
            raise ValueError(f"source leakage in fold {fold}")
        if set(groups[train]) & set(groups[valid]):
            raise ValueError(f"automatic group leakage in fold {fold}")
        if set(y[train]) != {0, 1} or set(y[valid]) != {0, 1}:
            raise ValueError(f"fold {fold} lacks one class; source-level metric unavailable")
        fold_source[fold] = next(iter(heldout_sources))

    specs = candidate_specs()
    args.output.mkdir(parents=True)
    fold_rows, oof_rows, feature_rows, issues = [], [], [], []
    started = time.perf_counter()
    for fold, (train, valid) in enumerate(split_list, 1):
        t0 = time.perf_counter()
        matrices, feature_meta = vectorize_fold([texts[i] for i in train],
                                                [texts[i] for i in valid], SEED + 100 + fold)
        vector_seconds = time.perf_counter() - t0
        feature_rows.append({"fold": fold, "heldout_source": fold_source[fold],
                             "train_n": len(train), "valid_n": len(valid),
                             "train_positive": int(y[train].sum()),
                             "valid_positive": int(y[valid].sum()), **feature_meta})
        print(f"Source fold {fold}/{len(split_list)}: holding out {fold_source[fold]} "
              f"({len(valid)} articles)")
        for spec in specs:
            model = make_model(spec, SEED + 100 + fold)
            X_train, X_valid = matrices.get(spec["features"], (None, None))
            if X_train is None:
                X_train, X_valid = np.zeros((len(train), 1)), np.zeros((len(valid), 1))
            try:
                t1 = time.perf_counter()
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    model.fit(X_train, y[train])
                    pred = model.predict(X_valid).astype(int)
                    scores, score_kind = get_scores(model, X_valid, pred)
                elapsed = time.perf_counter() - t1
                row_metrics = metrics(y[valid], pred, scores)
                fold_rows.append({"model": spec["name"], "features": spec["features"],
                                  "kind": spec["kind"], "fold": fold,
                                  "heldout_source": fold_source[fold], "train_n": len(train),
                                  "valid_n": len(valid), "fit_predict_seconds": elapsed,
                                  "vectorize_seconds_shared": vector_seconds,
                                  "score_kind": score_kind, **row_metrics})
                for local, idx in enumerate(valid):
                    oof_rows.append({"model": spec["name"], "fold": fold,
                                     "heldout_source": fold_source[fold],
                                     "request_article_id": rows[idx]["request_article_id"],
                                     "physical_article_id": rows[idx]["physical_article_id"],
                                     "label_origin": rows[idx]["label_origin"],
                                     "y_true": int(y[idx]), "y_pred": int(pred[local]),
                                     "score": float(scores[local]), "score_kind": score_kind})
                for warning in caught:
                    issues.append({"model": spec["name"], "fold": fold,
                                   "warning": f"{type(warning.message).__name__}: {warning.message}"})
            except Exception as error:
                issues.append({"model": spec["name"], "fold": fold,
                               "error": f"{type(error).__name__}: {error}"})
                print(f"  FAILED {spec['name']}: {type(error).__name__}: {error}")

    by_model = defaultdict(list)
    for row in fold_rows:
        by_model[row["model"]].append(row)
    by_oof = defaultdict(list)
    for row in oof_rows:
        by_oof[row["model"]].append(row)
    summary = []
    for spec in specs:
        runs, predictions = by_model[spec["name"]], by_oof[spec["name"]]
        if len(runs) != len(split_list) or len(predictions) != len(rows):
            issues.append({"model": spec["name"],
                           "error": f"incomplete coverage: folds={len(runs)}, rows={len(predictions)}"})
            continue
        yy = np.asarray([r["y_true"] for r in predictions])
        pp = np.asarray([r["y_pred"] for r in predictions])
        ss = np.asarray([r["score"] for r in predictions])
        pooled = metrics(yy, pp, ss)
        summary.append({"model": spec["name"], "features": spec["features"],
                        "kind": spec["kind"], "params": json.dumps(spec["params"], sort_keys=True),
                        "mean_source_f1": float(np.mean([r["f1_positive"] for r in runs])),
                        "std_source_f1": float(np.std([r["f1_positive"] for r in runs], ddof=1)),
                        "mean_source_recall": float(np.mean([r["recall_positive"] for r in runs])),
                        "mean_source_precision": float(np.mean([r["precision_positive"] for r in runs])),
                        "mean_source_ap": float(np.mean([r["average_precision"] for r in runs])),
                        "source_min_f1": min(r["f1_positive"] for r in runs),
                        "source_max_f1": max(r["f1_positive"] for r in runs),
                        "pooled_oof_f1": pooled["f1_positive"],
                        "pooled_oof_precision": pooled["precision_positive"],
                        "pooled_oof_recall": pooled["recall_positive"],
                        "pooled_oof_ap": pooled["average_precision"],
                        "pooled_oof_mcc": pooled["mcc"], "pooled_oof_fn": pooled["fn"],
                        "pooled_oof_fp": pooled["fp"],
                        "fit_predict_seconds_total": float(sum(r["fit_predict_seconds"] for r in runs))})
    summary.sort(key=lambda r: (-r["mean_source_f1"], -r["mean_source_ap"]))
    write_csv(args.output / "summary.csv", summary)
    write_csv(args.output / "fold-metrics.csv", fold_rows)
    write_csv(args.output / "oof-predictions.csv", oof_rows)
    write_csv(args.output / "fold-features.csv", feature_rows)
    write_csv(args.output / "warnings-and-failures.csv", issues)
    elapsed = time.perf_counter() - started
    source_counts = {source: {str(label): count for label, count in Counter(
        y[sources == source]).items()} for source in sorted(set(sources))}
    manifest = {"schema_version": "adverse-source-heldout-screen-v1", "status": "completed",
                "data_file": str(args.data), "data_sha256": file_digest(args.data),
                "runner_sha256": file_digest(Path(__file__)), "python": platform.python_version(),
                "numpy": np.__version__, "scipy": scipy.__version__, "scikit_learn": sklearn.__version__,
                "groups": "automatic_group_id", "group_count": len(set(groups)),
                "source_count": len(set(sources)), "folds": len(split_list),
                "fold_strategy": "LeaveOneGroupOut(source_name)",
                "heldout_sources": [fold_source[i] for i in sorted(fold_source)],
                "source_label_counts": source_counts,
                "semantic_features": ["title", "body"], "metadata_excluded_from_features": True,
                "candidates_configured": len(specs), "candidates_completed": len(summary),
                "elapsed_seconds": elapsed, "same_case_review_pending": True,
                "labels_are_teacher_derived_and_not_independent_truth": True,
                "production_accuracy_claim": False,
                "interpretation": "Descriptive publisher-transfer stress test; source sizes and label balance differ substantially."}
    (args.output / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Source-held-out classical model screen", "",
             f"- Data: {len(rows)} teacher-/human-adjudicated articles; {len(set(sources))} publishers.",
             f"- Validation: leave one publisher out per fold; {len(summary)} of {len(specs)} configurations completed.",
             f"- Elapsed seconds: {elapsed:.1f}.",
             "- Each publisher is excluded entirely from that fold's model fitting and text-feature fitting.",
             "- Publisher class balance and sample sizes vary widely; per-source results are descriptive.",
             "- This remains evaluation against mostly teacher-derived labels, not independent production truth.",
             "", "## Top 15 by mean publisher F1", "",
             "| Model | Mean publisher F1 ± SD | Recall | Precision | AP | Worst publisher F1 | Pooled OOF F1 | FN | FP |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in summary[:15]:
        lines.append(f"| {r['model']} | {r['mean_source_f1']:.3f} ± {r['std_source_f1']:.3f} | "
                     f"{r['mean_source_recall']:.3f} | {r['mean_source_precision']:.3f} | {r['mean_source_ap']:.3f} | "
                     f"{r['source_min_f1']:.3f} | {r['pooled_oof_f1']:.3f} | {r['pooled_oof_fn']} | {r['pooled_oof_fp']} |")
    lines += ["", "Full rankings: `summary.csv`; publisher-by-publisher behavior: `fold-metrics.csv`; "
              "per-article held-out predictions: `oof-predictions.csv`.", ""]
    (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"candidate_count": len(specs), "completed": len(summary),
                      "issues": len(issues), "elapsed_seconds": round(elapsed, 1),
                      "top5": [{"model": r["model"], "mean_source_f1": round(r["mean_source_f1"], 4),
                                "mean_source_ap": round(r["mean_source_ap"], 4),
                                "source_min_f1": round(r["source_min_f1"], 4)} for r in summary[:5]]}, indent=2))


if __name__ == "__main__":
    main()
