"""Frozen MiniLM embeddings + independent logistic classifiers for VV news.

CPU only. Reads train/validation only. Every article is split into bounded,
non-overlapping character spans, retaining its complete original text. Chunk
embeddings are token-weighted, averaged and normalized. The encoder is NOT
fine-tuned; only the logistic heads learn from known (-1 excluded) train targets.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import time
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score

try:
    from audit_dataset import LABEL_NAMES, file_hash
    from prepare_dataset import json_lines, write_atomic
    from train_tfidf_lr import CONFIG as LR_CONFIG
    from train_tfidf_lr import load_partition, counts_and_metrics, save_model, format_metric
except ModuleNotFoundError as error:
    if error.name not in {"audit_dataset", "prepare_dataset", "train_tfidf_lr"}:
        raise
    raise SystemExit("ERROR: put this file alongside the previous experiment scripts.") from error


MODEL_VERSION = "minilm-chunked-lr-silver-v1"
ENCODER_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# Pin the encoder, rather than silently following an evolving Hub main branch.
ENCODER_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
TOKEN_BUDGET = 240
DIMENSION = 384
THRESHOLD = 0.5
CONFIG = {
    "encoder": {"name": ENCODER_NAME, "revision": ENCODER_REVISION,
                "device": "cpu", "dimension": DIMENSION, "fine_tuned": False},
    "representation": {"version": "full-text-character-spans-token-weighted-mean-v1",
                       "chunk_token_budget": TOKEN_BUDGET, "overlap": 0,
                       "chunk_limit": None, "chunk_normalization": "L2",
                       "article_pooling": "token_count_weighted_mean", "article_normalization": "L2",
                       "input": "prepared title plus body text"},
    "classifier": deepcopy(LR_CONFIG["classifier"]),
    "initial_threshold": THRESHOLD, "unknown_target": -1,
}


def chunk_text(text: str, tokenizer, budget: int = TOKEN_BUDGET) -> list[tuple[str, int]]:
    """Preserve the full text, recheck each span after tokenizing its boundaries.

Splitting inside a multi-piece word can change its tokenization. Retokenizing
each candidate checks the actual model input length instead of assuming that
a character slice has the same number of tokens as the original full text.
"""
    if not isinstance(text, str) or not text.strip() or budget < 1:
        raise ValueError("Chunking requires nonempty text and a positive token budget.")
    if not tokenizer.is_fast:
        raise ValueError("This chunker requires MiniLM's fast tokenizer for character offsets.")
    encoded = tokenizer(text, add_special_tokens=False, truncation=False,
                        return_offsets_mapping=True, return_attention_mask=False,
                        return_token_type_ids=False, verbose=False)
    offsets = encoded["offset_mapping"]
    if not offsets:
        raise ValueError("Article has no usable text tokens.")
    if any(end <= start for start, end in offsets):
        raise ValueError("Unexpected empty tokenizer offsets.")
    chunks, index, cursor = [], 0, 0
    while index < len(offsets):
        next_index = min(index + budget, len(offsets))
        while True:
            end = len(text) if next_index == len(offsets) else offsets[next_index - 1][1]
            span = text[cursor:end]
            token_count = len(tokenizer(span, add_special_tokens=False, truncation=False,
                                        return_attention_mask=False, return_token_type_ids=False,
                                        verbose=False)["input_ids"])
            if 0 < token_count <= budget:
                break
            next_index -= 1
            if next_index <= index:
                raise ValueError("Could not create a bounded, nonempty chunk.")
        chunks.append((span, token_count))
        cursor, index = end, next_index
    if cursor != len(text) or "".join(span for span, _ in chunks) != text:
        raise ValueError("Chunking did not preserve the entire input text.")
    return chunks


def build_embeddings(texts: list[str], encoder, batch_size: int,
                     partition_name: str) -> tuple[np.ndarray, dict, list[int]]:
    """Feature builder accepts text only; no annotation values or labels."""
    chunks, article_indices, weights, chunk_counts = [], [], [], []
    for index, text in enumerate(texts):
        spans = chunk_text(text, encoder.tokenizer)
        chunk_counts.append(len(spans))
        for span, token_count in spans:
            chunks.append(span)
            article_indices.append(index)
            weights.append(token_count)
    article_indices = np.asarray(article_indices, dtype=np.int64)
    weights = np.asarray(weights, dtype=np.float64)
    sums = np.zeros((len(texts), DIMENSION), dtype=np.float64)
    weight_sums = np.zeros(len(texts), dtype=np.float64)
    print(f"{partition_name}: {len(texts)} articles, {len(chunks)} chunks; encoding on CPU...", flush=True)
    tick = time.perf_counter()
    last_update = tick
    for start in range(0, len(chunks), batch_size):
        stop = min(start + batch_size, len(chunks))
        vectors = np.asarray(encoder.encode(chunks[start:stop], batch_size=batch_size,
                             convert_to_numpy=True, normalize_embeddings=True,
                             show_progress_bar=False), dtype=np.float64)
        if vectors.shape != (stop - start, DIMENSION) or not np.all(np.isfinite(vectors)):
            raise ValueError("Encoder returned invalid embeddings.")
        if not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=0.0001):
            raise ValueError("Chunk embeddings were not normalized.")
        np.add.at(sums, article_indices[start:stop], vectors * weights[start:stop, None])
        np.add.at(weight_sums, article_indices[start:stop], weights[start:stop])
        now = time.perf_counter()
        if now - last_update >= 15 or stop == len(chunks):
            print(f"{partition_name} chunks completed: {stop}/{len(chunks)} ({now - tick:.1f}s)", flush=True)
            last_update = now
    if np.any(weight_sums <= 0):
        raise ValueError("Some articles received no embeddings.")
    means = sums / weight_sums[:, None]
    norms = np.linalg.norm(means, axis=1, keepdims=True)
    if not np.all(np.isfinite(norms)) or np.any(norms < 1e-12):
        raise ValueError("Article embedding could not be normalized.")
    features = (means / norms).astype(np.float32)
    stats = {"articles": len(texts), "chunks": len(chunks),
             "multi_chunk_articles": sum(c > 1 for c in chunk_counts),
             "maximum_chunks_per_article": max(chunk_counts),
             "tokens_encoded_including_boundary_retokenization": int(weight_sums.sum()),
             "encoding_seconds": time.perf_counter() - tick,
             "all_input_characters_preserved": True}
    return features, stats, chunk_counts


def evaluate(truth: np.ndarray, scores: np.ndarray) -> tuple[dict, dict]:
    known, available = truth != -1, np.isfinite(scores)
    evaluated = known & available
    if not np.any(evaluated):
        raise ValueError("No validation pairs can be evaluated.")
    predicted = scores >= THRESHOLD
    per_label, f1s = {}, []
    for index, label in enumerate(LABEL_NAMES):
        mask = evaluated[:, index]
        target, probabilities = truth[mask, index], scores[mask, index]
        metrics = counts_and_metrics(target, probabilities >= THRESHOLD) if np.any(mask) else None
        if metrics is not None:
            metrics["average_precision"] = float(average_precision_score(target, probabilities)) if (
                np.any(target == 1) and np.any(target == 0)) else None
            if np.any(target == 1) and metrics["f1"] is not None:
                f1s.append(metrics["f1"])
        per_label[label] = metrics
    fully_known = np.all(evaluated, axis=1)
    summary = {
        "evaluated_known_pairs": int(np.sum(evaluated)),
        "validation_unknown_pairs_excluded": int(np.sum(~known)),
        "known_pairs_without_classifier": int(np.sum(known & ~available)),
        "validation_micro": counts_and_metrics(truth[evaluated], predicted[evaluated]),
        "validation_macro_f1_positive_supported_labels": float(np.mean(f1s)) if f1s else None,
        "macro_f1_label_count": len(f1s),
        "fully_known_and_scored_articles": int(np.sum(fully_known)),
        "exact_match_fully_known_and_scored_articles": float(np.mean(np.all(predicted[fully_known] == truth[fully_known], axis=1))) if np.any(fully_known) else None,
    }
    return summary, per_label


def main() -> int:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits-dir", type=Path, default=base / "data" / "processed" / "splits")
    parser.add_argument("--lr-dir", type=Path, default=base / "outputs" / "tfidf_lr_baseline_v1")
    parser.add_argument("--svm-dir", type=Path, default=base / "outputs" / "tfidf_svm_baseline_v1")
    parser.add_argument("--output-dir", type=Path, default=base / "outputs" / "embedding_lr_baseline_v1")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.batch_size < 1 or args.threads < 1:
        parser.error("Batch size and thread count must be positive.")
    started = time.perf_counter()
    try:
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise ValueError("Output directory contains files. Preserve this run; use --output-dir with a new directory.")
        manifest_path = args.splits_dir / "split_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["labels"] != list(LABEL_NAMES) or manifest["target_encoding"] != {
            "positive": 1, "provisional_negative": 0, "unknown": -1
        }:
            raise ValueError("Unexpected frozen label order or encoding.")
        train, train_y = load_partition(args.splits_dir, "train", manifest)
        validation, validation_y = load_partition(args.splits_dir, "validation", manifest)
        for key in ("news_id", "body_hash"):
            if {r[key] for r in train} & {r[key] for r in validation}:
                raise ValueError(f"Train/validation overlap: {key}.")
        if {v for r in train for v in r["original_news_ids"]} & {v for r in validation for v in r["original_news_ids"]}:
            raise ValueError("Train/validation original ID overlap.")
        lineage = {
            "input_dataset_sha256": manifest["input_dataset_sha256"],
            "split_manifest_sha256": file_hash(manifest_path),
            "train_sha256": manifest["splits"]["train"]["sha256"],
            "validation_sha256": manifest["splits"]["validation"]["sha256"],
            "preprocessing_version": manifest["input_preprocessing_version"],
            "label_source": manifest["label_source"],
        }
        baselines = {}
        for name, directory, version in (("tfidf_lr", args.lr_dir, "tfidf-lr-silver-baseline-v1"),
                                         ("tfidf_svm", args.svm_dir, "tfidf-linear-svm-silver-v1")):
            path = directory / "training_report.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            if report["model_version"] != version or report["labels"] != list(LABEL_NAMES) or report["lineage"] != lineage:
                raise ValueError(f"Baseline {name} data lineage or version differs.")
            if name == "tfidf_lr" and report["config"]["classifier"] != CONFIG["classifier"]:
                raise ValueError("Logistic settings differ from the original baseline.")
            baselines[name] = {"report": report, "report_sha256": file_hash(path)}

        # Heavy imports only after checking input files and comparison lineage.
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(args.threads)
        torch.manual_seed(42)
        np.random.seed(42)
        print(f"Train news: {len(train)}", flush=True)
        print(f"Validation news: {len(validation)}", flush=True)
        print("Loading pinned MiniLM encoder on CPU (no fine-tuning)...", flush=True)
        tick = time.perf_counter()
        encoder = SentenceTransformer(ENCODER_NAME, revision=ENCODER_REVISION,
                                      device="cpu", trust_remote_code=False)
        encoder.eval()
        encoder.max_seq_length = 256
        if encoder.get_embedding_dimension() != DIMENSION:
            raise ValueError("Unexpected MiniLM embedding dimension.")
        special_tokens = encoder.tokenizer.num_special_tokens_to_add(pair=False)
        if TOKEN_BUDGET + special_tokens > encoder.max_seq_length:
            raise ValueError("Chunk budget exceeds encoder sequence limit.")
        encoder_load_seconds = time.perf_counter() - tick
        train_x, train_stats, train_chunks = build_embeddings([r["text"] for r in train], encoder, args.batch_size, "TRAIN")
        validation_x, validation_stats, validation_chunks = build_embeddings([r["text"] for r in validation], encoder, args.batch_size, "VALIDATION")

        classifiers, per_label = {}, {}
        scores = np.full(validation_y.shape, np.nan, dtype=np.float64)
        fit_seconds = predict_seconds = 0.0
        for index, label in enumerate(LABEL_NAMES):
            mask = train_y[:, index] != -1
            target = train_y[mask, index]
            positive, negative = int(np.sum(target == 1)), int(np.sum(target == 0))
            support = {
                "train_positive": positive, "train_provisional_negative": negative,
                "train_unknown_excluded": int(np.sum(~mask)),
                "validation_positive": int(np.sum(validation_y[:, index] == 1)),
                "validation_provisional_negative": int(np.sum(validation_y[:, index] == 0)),
                "validation_unknown_excluded": int(np.sum(validation_y[:, index] == -1)),
                "low_training_positive_support": positive < 20, "threshold": THRESHOLD,
            }
            for b in baselines.values():
                if any(b["report"]["per_label"][label][k] != support[k] for k in support if k.startswith(("train_", "validation_"))):
                    raise ValueError(f"Baseline target support differs for {label}.")
            if not positive or not negative:
                classifiers[label] = None
                per_label[label] = {**support, "status": "skipped_missing_training_class"}
                continue
            classifier = LogisticRegression(**CONFIG["classifier"])
            tick = time.perf_counter()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                classifier.fit(train_x[mask], target)
            if any(issubclass(w.category, ConvergenceWarning) for w in caught):
                raise ValueError(f"Logistic head did not converge for {label}.")
            fit_seconds += time.perf_counter() - tick
            tick = time.perf_counter()
            scores[:, index] = classifier.predict_proba(validation_x)[:, list(classifier.classes_).index(1)]
            predict_seconds += time.perf_counter() - tick
            if not np.all(np.isfinite(scores[:, index])):
                raise ValueError(f"Non-finite probabilities for {label}.")
            classifiers[label] = classifier
            per_label[label] = {**support, "status": "trained_experimental"}
            print(f"[{index + 1}/23] {label}: positive={positive}, negative={negative}, unknown excluded={support['train_unknown_excluded']}", flush=True)
        summary, metrics = evaluate(validation_y, scores)
        for label in LABEL_NAMES:
            per_label[label]["metrics"] = metrics[label]
        summary.update({
            "trained_classifiers": sum(c is not None for c in classifiers.values()),
            "skipped_classifiers": sum(c is None for c in classifiers.values()),
            "embedding_dimension": DIMENSION, "encoder_load_seconds": encoder_load_seconds,
            "classifier_fit_seconds": fit_seconds,
            "validation_predict_seconds_excluding_encoding": predict_seconds,
        })
        comparisons = {}
        for name, b in baselines.items():
            old = b["report"]
            for key in ("evaluated_known_pairs", "known_pairs_without_classifier", "macro_f1_label_count"):
                if old["summary"][key] != summary[key]:
                    raise ValueError(f"Evaluation coverage differs from {name}: {key}.")
            ap_labels = [l for l in LABEL_NAMES if metrics[l] is not None and old["per_label"][l]["metrics"] is not None
                         and metrics[l]["average_precision"] is not None
                         and old["per_label"][l]["metrics"]["average_precision"] is not None]
            comparisons[name] = {
                "baseline_model_version": old["model_version"], "baseline_report_sha256": b["report_sha256"],
                "baseline_micro": old["summary"]["validation_micro"], "embedding_micro": summary["validation_micro"],
                "baseline_macro_f1": old["summary"]["validation_macro_f1_positive_supported_labels"],
                "embedding_macro_f1": summary["validation_macro_f1_positive_supported_labels"],
                "average_precision_labels": ap_labels,
                "baseline_macro_average_precision": float(np.mean([old["per_label"][l]["metrics"]["average_precision"] for l in ap_labels])) if ap_labels else None,
                "embedding_macro_average_precision": float(np.mean([metrics[l]["average_precision"] for l in ap_labels])) if ap_labels else None,
                "interpretation": "Same frozen data and known targets; default thresholds. No winner selected.",
            }
        environment = {"python": platform.python_version(), **{n: importlib.metadata.version(n) for n in
                       ("numpy", "scipy", "scikit-learn", "joblib", "torch", "transformers", "sentence-transformers", "huggingface-hub", "tokenizers")}}
        config = deepcopy(CONFIG)
        config["execution"] = {"batch_size": args.batch_size, "torch_threads": args.threads, "seed": 42}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        print("Saving encoder, embedding cache and classifier heads...", flush=True)
        encoder.save(str(args.output_dir / "encoder"), create_model_card=False, safe_serialization=True)
        encoder_files = {p.relative_to(args.output_dir / "encoder").as_posix(): file_hash(p)
                         for p in sorted((args.output_dir / "encoder").rglob("*")) if p.is_file()}
        cache_hash = save_model(args.output_dir / "embedding_cache.joblib", {
            "config": config, "lineage": lineage,
            "train_news_ids": [r["news_id"] for r in train], "train_embeddings": train_x,
            "validation_news_ids": [r["news_id"] for r in validation], "validation_embeddings": validation_x,
        })
        bundle = {
            "model_version": MODEL_VERSION, "classifiers": classifiers, "labels": list(LABEL_NAMES),
            "thresholds": {l: THRESHOLD for l in LABEL_NAMES}, "config": config,
            "encoder_relative_path": "encoder", "encoder_file_sha256": encoder_files,
            "representation_function": "train_embedding_lr.build_embeddings",
            "lineage": lineage, "environment": environment,
            "label_training_support": {l: {k: v for k, v in r.items() if k.startswith("train_") or k == "low_training_positive_support"} for l, r in per_label.items()},
            "deployment_status": "experimental_not_promoted",
        }
        model_hash = save_model(args.output_dir / "model.joblib", bundle)
        predictions = [{
            "news_id": r["news_id"], "excel_row": r["excel_row"], "title": r["title"], "text": r["text"],
            "reference_shared_labels": r["shared_labels"], "reference_label_values": r["label_values"],
            "chunk_count": validation_chunks[i],
            "predicted_labels": [l for j, l in enumerate(LABEL_NAMES) if np.isfinite(scores[i, j]) and scores[i, j] >= THRESHOLD],
            "raw_probability_by_label": {l: float(scores[i, j]) if np.isfinite(scores[i, j]) else None for j, l in enumerate(LABEL_NAMES)},
        } for i, r in enumerate(validation)]
        predictions_hash = write_atomic(args.output_dir / "validation_predictions.jsonl", json_lines(predictions))
        chunking_hash = write_atomic(args.output_dir / "chunking_report.json", json.dumps({
            "representation": config["representation"], "train": train_stats, "validation": validation_stats,
            "train_chunk_counts_by_news_id": {r["news_id"]: c for r, c in zip(train, train_chunks)},
            "validation_chunk_counts_by_news_id": {r["news_id"]: c for r, c in zip(validation, validation_chunks)},
        }, ensure_ascii=False, indent=2) + "\n")
        report = {
            "schema_version": 1, "model_version": MODEL_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "labels": list(LABEL_NAMES), "config": config, "environment": environment, "lineage": lineage,
            "train_news": len(train), "validation_news": len(validation), "summary": summary,
            "per_label": per_label, "comparison_to_baselines": comparisons,
            "chunking_summary": {"train": train_stats, "validation": validation_stats},
            "artifacts": {"model_sha256": model_hash, "validation_predictions_sha256": predictions_hash,
                          "embedding_cache_sha256": cache_hash, "chunking_report_sha256": chunking_hash,
                          "encoder_file_sha256": encoder_files, "script_sha256": file_hash(Path(__file__))},
            "elapsed_seconds": time.perf_counter() - started,
            "limitations": [
                "Metrics describe agreement with silver consensus labels on known pairs, not human-reviewed accuracy.",
                "Frozen general-purpose English encoder is not specialized for the VV taxonomy.",
                "All raw text is covered, but chunk averaging can dilute localized category evidence and lose cross-chunk context.",
                "Encoder is frozen; classifier heads alone are fitted, using train targets only.",
                "Threshold 0.5 is untuned; probabilities are not calibrated guarantees or risk severity.",
                "Default model decision rules are not matched precision operating points.",
                "Rare-label scores are unstable with small support; macro-F1 excludes labels without positive validation support.",
                "Test contents were not read. Repeated validation comparisons can overfit model selection.",
                "Experimental model; no production promotion or model winner selected.",
            ],
        }
        write_atomic(args.output_dir / "training_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        micro = summary["validation_micro"]
        print("\nEMBEDDING TRAINING COMPLETED")
        print(f"Trained classifiers: {summary['trained_classifiers']}/23")
        for name in ("precision", "recall", "f1"):
            print(f"Validation micro {name}: {format_metric(micro[name])}")
        print(f"Validation macro F1: {format_metric(summary['validation_macro_f1_positive_supported_labels'])}")
        print(f"Macro F1 label count: {summary['macro_f1_label_count']}/23")
        for name, c in comparisons.items():
            print(f"Micro F1 ({name} -> embedding LR): {format_metric(c['baseline_micro']['f1'])} -> {format_metric(micro['f1'])}")
            print(f"Macro average precision ({name} -> embedding LR): {format_metric(c['baseline_macro_average_precision'])} -> {format_metric(c['embedding_macro_average_precision'])} ({len(c['average_precision_labels'])}/23 labels)")
        print(f"Validation unknown pairs excluded: {summary['validation_unknown_pairs_excluded']}")
        print(f"Train chunks: {train_stats['chunks']}; validation chunks: {validation_stats['chunks']}")
        print(f"Embedding dimension: {DIMENSION}; encoder fine-tuned: NO")
        print(f"Elapsed seconds: {report['elapsed_seconds']:.1f}")
        print("Test contents used: NO")
        print(f"Files saved to: {args.output_dir}")
        print("Consensus agreement only. Default thresholds; no winner selected.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
