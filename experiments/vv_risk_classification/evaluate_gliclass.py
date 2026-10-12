"""Evaluate frozen GLiClass on VV validation silver targets (no training).

English taxonomy definitions; fixed 0.5 threshold; unknown (-1) pairs masked.
Full article coverage via overlapping token-bounded character spans. Concise
English candidate groups are fixed before inference. Scores are not calibrated.
Never loads train/test contents. Requires the existing experiment helpers.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
import time

import numpy as np
from sklearn.metrics import average_precision_score

try:
    from audit_dataset import LABEL_NAMES, file_hash
    from prepare_dataset import write_atomic, json_lines
    from train_tfidf_lr import load_partition, counts_and_metrics, format_metric
except ModuleNotFoundError as error:
    if error.name not in {"audit_dataset", "prepare_dataset", "train_tfidf_lr"}:
        raise
    raise SystemExit("ERROR: place this file alongside the existing experiment scripts.") from error


MODEL_VERSION = "gliclass-modern-base-zeroshot-silver-v1"
MODEL_ID = "knowledgator/gliclass-modern-base-v3.0"
REVISION = "ac369222ca4375ca66ebaf7fb5220f223514c035"
THRESHOLD = 0.5
MAX_LENGTH = 1024
TEXT_TOKENS = 256
OVERLAP_TOKENS = 32
SPECIAL_MARKERS = ("<<LABEL>>", "<<SEP>>", "<<EXAMPLE>>")
ARABIC_SCRIPT = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
TASK_PROMPT = (
    "Classify supported facts or specific attributed allegations in this news excerpt. "
    "Mere keywords, negation, hypothetical scenarios or external knowledge are insufficient. "
    "Each category needs its own support and may concern a different actor. Status "
    "categories do not require wrongdoing. Zero or multiple categories are allowed.\n"
    "News excerpt:\n"
)
# Reviewed summaries of the supplied English descriptions, independent of news
# and targets. The digest rejects a changed taxonomy needing new summaries.
DESCRIPTION_SHA256 = "50706ae856d5ae2f6bb5c36af0e0db4735479a498e117d0504a45b5cb56639e5"
SHORT_DESCRIPTIONS = {
    "PEP": "A person holds or formerly held a prominent public or senior intergovernmental role.",
    "RCA": "A relative or known close associate of an identified politically exposed person.",
    "State-Owned": "An explicitly state-owned organization or significant government equity; control alone is insufficient.",
    "Jurisdiction Risk": "Reported AML/CFT jurisdiction risk status or entity exposure explicitly linked to that status.",
    "Sanctions": "Official sanctions designation, restrictions, lifting or amendment for an identified target; excludes procurement debarment.",
    "Terrorism / Terrorist Financing": "Terrorist activity, financing, group affiliation or material support with a reported terrorism link.",
    "Proliferation / Proliferation Financing": "Unlawful WMD proliferation, financing or support with a reported proliferation link; conventional arms alone are insufficient.",
    "Human Rights / Atrocities": "Attributed genocide, war crimes, crimes against humanity, or a specific grave human-rights abuse.",
    "Regulatory": "Target-specific regulatory inquiry, alleged breach, finding or enforcement, including procurement debarment; excludes general guidance.",
    "Law Enforcement": "Official criminal wanted status, investigation, arrest, search, warrant or seizure; excludes general police activity.",
    "Judicial / Court": "A criminal or civil court proceeding or outcome, including conviction, acquittal, sentencing or dismissal.",
    "Crime-Corruption": "Bribery, kickbacks, graft or entrusted-authority abuse for improper benefit; mentioning officials alone is insufficient.",
    "Crime-Fraud": "Financial deception or a specific fraud allegation; investment losses or unpaid debts alone are insufficient.",
    "Crime-Forgery & Counterfeiting": "Intentional forgery, counterfeiting, document falsification or knowing use of forged items; excludes accidental errors.",
    "Crime-Money Laundering": "Laundering or concealment of criminal proceeds; ordinary transfers or unexplained wealth alone are insufficient.",
    "Crime-Tax Crime": "Deliberate criminal tax evasion, fraud or false returns; excludes lawful tax avoidance and administrative errors.",
    "Crime-Securities & Market Crime": "Insider dealing, criminal market manipulation or securities fraud; excludes lawful trading and technical noncriminal breaches.",
    "Crime-Cybercrime": "Unauthorized digital attacks, hacking, ransomware, data theft or crimes materially enabled by digital systems.",
    "Crime-Trafficking & Smuggling": "Illicit trafficking or smuggling of people, drugs, weapons, goods or wildlife; excludes authorized trade.",
    "Crime-Organized Crime": "Organized criminal-group activity, membership or participation; several co-defendants or generic group wording alone are insufficient.",
    "Crime-Violent Crime": "Murder, assault, kidnapping, violent robbery or physical harm threats; excludes nonviolent extortion.",
    "Crime-Theft & Property Crime": "Unlawful theft, robbery, embezzlement or property appropriation; debt or nonpayment alone is insufficient.",
    "Crime-Environmental & Wildlife Crime": "Specific unlawful environmental or wildlife conduct, including illegal logging, mining, fishing or wildlife trafficking.",
}
BASELINES = {
    "tfidf_lr": ("tfidf_lr_baseline_v1", "tfidf-lr-silver-baseline-v1"),
    "tfidf_svm": ("tfidf_svm_baseline_v1", "tfidf-linear-svm-silver-v1"),
    "embedding_lr": ("embedding_lr_baseline_v1", "minilm-chunked-lr-silver-v1"),
    "embedding_svm": ("embedding_svm_baseline_v1", "minilm-chunked-linear-svm-silver-v1"),
}


def token_count(tokenizer, text: str, *, special: bool = False) -> int:
    return len(tokenizer(text, add_special_tokens=special, truncation=False,
                         return_attention_mask=False, return_token_type_ids=False,
                         verbose=False)["input_ids"])


def chunk_text(text: str, tokenizer) -> list[dict]:
    """Only text enters this function; offsets retain every input character."""
    if not isinstance(text, str) or not text.strip() or not tokenizer.is_fast:
        raise ValueError("Chunking requires nonempty text and a fast tokenizer.")
    if any(marker in text for marker in SPECIAL_MARKERS):
        raise ValueError("Article contains GLiClass control markers; inspect input.")
    encoded = tokenizer(text, add_special_tokens=False, truncation=False,
                        return_offsets_mapping=True, return_attention_mask=False,
                        return_token_type_ids=False, verbose=False)
    offsets = encoded["offset_mapping"]
    if not offsets or any(not 0 <= a <= b <= len(text) for a, b in offsets):
        raise ValueError("Invalid article token offsets.")
    if any(offsets[i][1] > offsets[i + 1][1] for i in range(len(offsets) - 1)):
        raise ValueError("Non-monotonic token end offsets.")
    chunks, index, frontier = [], 0, 0
    while index < len(offsets):
        start = 0 if not chunks else min(offsets[index][0], frontier)
        stop = min(index + TEXT_TOKENS, len(offsets))
        while stop > index:
            end = len(text) if stop == len(offsets) else offsets[stop - 1][1]
            span = text[start:end]
            count = token_count(tokenizer, span) if span else 0
            if 0 < count <= TEXT_TOKENS and end > frontier:
                break
            stop -= 1
        else:
            raise ValueError("Cannot make a bounded chunk with advancing coverage.")
        if start > frontier:
            raise ValueError("Chunk coverage has a gap.")
        chunks.append({"text": span, "start": start, "end": end, "tokens": count})
        frontier = end
        if end == len(text):
            break
        index = max(index + 1, stop - OVERLAP_TOKENS)
    if not chunks or chunks[0]["start"] != 0 or frontier != len(text):
        raise ValueError("Full article coverage was not established.")
    return chunks


def make_candidates(taxonomy: dict) -> list[str]:
    labels = taxonomy.get("labels", [])
    if taxonomy.get("schema_version") != 1 or taxonomy.get("label_count") != 23:
        raise ValueError("Unexpected taxonomy schema or label count.")
    if [r.get("name") for r in labels] != list(LABEL_NAMES):
        raise ValueError("Taxonomy names/order differ from the frozen dataset.")
    if taxonomy.get("preparation", {}).get("language") != "en":
        raise ValueError("Use the revised English-only taxonomy file.")
    if ARABIC_SCRIPT.search(json.dumps(taxonomy, ensure_ascii=False)):
        raise ValueError("Taxonomy contains Persian/Arabic-script content.")
    if len({r.get("stable_id") for r in labels}) != 23:
        raise ValueError("Taxonomy stable IDs must be unique.")
    descriptions = json.dumps([[r['name'], r['english_description']] for r in labels],
                              ensure_ascii=False, separators=(",", ":"))
    if hashlib.sha256(descriptions.encode("utf-8")).hexdigest() != DESCRIPTION_SHA256:
        raise ValueError("English definitions changed; prepare corresponding candidate summaries first.")
    candidates = []
    for row in labels:
        description = row.get("english_description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Missing English description: {row['name']}.")
        candidate = row["name"] + ": " + SHORT_DESCRIPTIONS[row["name"]]
        if any(marker in candidate for marker in SPECIAL_MARKERS):
            raise ValueError("Taxonomy contains model control markers.")
        candidates.append(candidate)
    return candidates


def pack_groups(candidates: list[str], classifier, tokenizer) -> list[list[int]]:
    """Pack complete summaries; reserve text space and a boundary margin."""
    limit = MAX_LENGTH - TEXT_TOKENS - 32
    groups, current = [], []
    for index in range(len(candidates)):
        trial = current + [index]
        formatted = classifier.pipe.prepare_input(
            "", [candidates[i] for i in trial], prompt=TASK_PROMPT)
        if token_count(tokenizer, formatted, special=True) > limit:
            if not current:
                raise ValueError("A complete candidate summary exceeds the budget.")
            groups.append(current)
            current = [index]
            formatted = classifier.pipe.prepare_input(
                "", [candidates[index]], prompt=TASK_PROMPT)
            if token_count(tokenizer, formatted, special=True) > limit:
                raise ValueError("A complete candidate summary exceeds the budget.")
        else:
            current = trial
    if current:
        groups.append(current)
    if [i for group in groups for i in group] != list(range(23)):
        raise ValueError("Candidate groups do not cover all 23 targets exactly once.")
    return groups


def score_group(chunks: list[dict], owners: list[int], local_indices: list[int],
                candidates: list[str], classifier, tokenizer, torch,
                article_count: int, batch_size: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Model receives raw spans and fixed definitions, never reference labels."""
    scores = np.full((article_count, len(candidates)), -np.inf)
    best_chunks = np.zeros(scores.shape, dtype=np.int32)
    maximum_input_tokens = 0
    started = last_print = time.perf_counter()
    for start in range(0, len(chunks), batch_size):
        stop = min(start + batch_size, len(chunks))
        batch = [c["text"] for c in chunks[start:stop]]
        # The library uses truncation=True. Compare its actual input IDs with
        # complete untruncated IDs before inference, rather than trusting a budget.
        formatted = [classifier.pipe.prepare_input(t, candidates, prompt=TASK_PROMPT)
                     for t in batch]
        complete = tokenizer(formatted, truncation=False, padding="longest",
                             return_tensors="pt")
        lengths = complete["attention_mask"].sum(dim=1)
        maximum_input_tokens = max(maximum_input_tokens, int(lengths.max().item()))
        if int(lengths.max().item()) > MAX_LENGTH:
            raise ValueError("Formatted model input would be truncated; no prediction made.")
        actual = classifier.pipe.prepare_inputs(batch, candidates, same_labels=True,
                                                prompt=TASK_PROMPT)
        if not torch.equal(complete["input_ids"], actual["input_ids"].cpu()):
            raise ValueError("Library input differs from the complete formatted input.")
        with torch.inference_mode():
            results = classifier(batch, candidates, threshold=0.0,
                                 batch_size=len(batch), prompt=TASK_PROMPT)
        if len(results) != len(batch):
            raise ValueError("Unexpected inference batch length.")
        for offset, result in enumerate(results):
            if len(result) != len(candidates):
                raise ValueError("Model did not return every candidate score.")
            mapped = {r["label"]: float(r["score"]) for r in result}
            if set(mapped) != set(candidates):
                raise ValueError("Model candidate names/coverage differ.")
            values = np.asarray([mapped[c] for c in candidates])
            if not np.all(np.isfinite(values)) or np.any((values < 0) | (values > 1)):
                raise ValueError("Invalid model scores.")
            index = start + offset
            owner = owners[index]
            improved = values > scores[owner]
            scores[owner] = np.maximum(scores[owner], values)
            best_chunks[owner, improved] = local_indices[index] + 1
        now = time.perf_counter()
        if now - last_print >= 15 or stop == len(chunks):
            print(f"  chunks: {stop}/{len(chunks)}; elapsed={now - started:.1f}s", flush=True)
            last_print = now
    if not np.all(np.isfinite(scores)) or np.any(best_chunks < 1):
        raise ValueError("Some article/category pairs have no scores.")
    return scores, best_chunks, maximum_input_tokens


def evaluate(truth: np.ndarray, scores: np.ndarray) -> tuple[dict, dict]:
    if scores.shape != truth.shape or not np.all(np.isfinite(scores)):
        raise ValueError("Incomplete score matrix.")
    known = truth != -1
    predicted = scores >= THRESHOLD
    per_label, f1s, aps = {}, [], []
    for index, label in enumerate(LABEL_NAMES):
        mask = known[:, index]
        target, values = truth[mask, index], scores[mask, index]
        metrics = counts_and_metrics(target, values >= THRESHOLD)
        metrics["average_precision"] = float(average_precision_score(target, values)) if (
            np.any(target == 1) and np.any(target == 0)) else None
        if np.any(target == 1) and metrics["f1"] is not None:
            f1s.append(metrics["f1"])
        if metrics["average_precision"] is not None:
            aps.append(metrics["average_precision"])
        per_label[label] = {
            "validation_positive": int(np.sum(truth[:, index] == 1)),
            "validation_provisional_negative": int(np.sum(truth[:, index] == 0)),
            "validation_unknown_excluded": int(np.sum(~mask)),
            "threshold": THRESHOLD, "status": "zero_shot_experimental", "metrics": metrics,
        }
    fully_known = np.all(known, axis=1)
    summary = {
        "evaluated_labels": len(LABEL_NAMES), "encoder_fine_tuned": False,
        "trained_classifiers": 0, "evaluated_known_pairs": int(np.sum(known)),
        "validation_unknown_pairs_excluded": int(np.sum(~known)),
        "known_pairs_without_classifier": 0,
        "validation_micro": counts_and_metrics(truth[known], predicted[known]),
        "validation_macro_f1_positive_supported_labels": float(np.mean(f1s)) if f1s else None,
        "macro_f1_label_count": len(f1s),
        "validation_macro_average_precision": float(np.mean(aps)) if aps else None,
        "average_precision_label_count": len(aps),
        "fully_known_and_scored_articles": int(np.sum(fully_known)),
        "exact_match_fully_known_and_scored_articles": float(np.mean(
            np.all(predicted[fully_known] == truth[fully_known], axis=1))) if np.any(fully_known) else None,
    }
    return summary, per_label


def read_baselines(root: Path, lineage: dict, truth: np.ndarray) -> dict:
    results = {}
    for short, (folder, version) in BASELINES.items():
        path = root / folder / "training_report.json"
        report = json.loads(path.read_text(encoding="utf-8-sig"))
        if (report["model_version"] != version or report["labels"] != list(LABEL_NAMES)
                or report["lineage"] != lineage):
            raise ValueError(f"Baseline identity/data lineage differs: {short}.")
        for index, label in enumerate(LABEL_NAMES):
            for value, key in [(1, "validation_positive"), (0, "validation_provisional_negative"),
                               (-1, "validation_unknown_excluded")]:
                if report["per_label"][label][key] != int(np.sum(truth[:, index] == value)):
                    raise ValueError(f"Baseline validation support differs: {short}/{label}.")
        if report["summary"]["evaluated_known_pairs"] != int(np.sum(truth != -1)):
            raise ValueError(f"Baseline evaluation coverage differs: {short}.")
        results[short] = {"report": report, "report_sha256": file_hash(path)}
    return results


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main() -> int:
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits-dir", type=Path, default=base / "data/processed/splits")
    parser.add_argument("--taxonomy", type=Path, default=base / "vv_label_definitions_v1.json")
    parser.add_argument("--baselines-root", type=Path, default=base / "outputs")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--pilot-size", type=int, default=0,
                        help="Technical pilot on first N validation records; no metrics or ranking.")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--resume", action="store_true", help="Resume verified complete candidate groups.")
    args = parser.parse_args()
    if args.batch_size < 1 or args.threads < 1 or args.pilot_size < 0:
        parser.error("Batch size/threads must be positive and pilot size nonnegative.")
    if args.output_dir is None:
        args.output_dir = base / "outputs" / ("gliclass_pilot_v1" if args.pilot_size else "gliclass_zeroshot_v1")
    started = time.perf_counter()
    try:
        checkpoint_path = args.output_dir / "checkpoint.json"
        if any((args.output_dir / name).exists() for name in ("evaluation_report.json", "pilot_report.json")):
            raise ValueError("Evaluation is complete. Preserve it or choose a new --output-dir.")
        if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.resume:
            raise ValueError("Output directory is nonempty. Use --resume for this exact unfinished run.")
        if args.resume and not checkpoint_path.is_file():
            raise ValueError("--resume requires an existing checkpoint.json.")
        manifest_path = args.splits_dir / "split_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if manifest["labels"] != list(LABEL_NAMES) or manifest["target_encoding"] != {
            "positive": 1, "provisional_negative": 0, "unknown": -1
        }:
            raise ValueError("Unexpected frozen labels or encoding.")
        validation, truth = load_partition(args.splits_dir, "validation", manifest)
        lineage = {
            "input_dataset_sha256": manifest["input_dataset_sha256"],
            "split_manifest_sha256": file_hash(manifest_path),
            "train_sha256": manifest["splits"]["train"]["sha256"],
            "validation_sha256": manifest["splits"]["validation"]["sha256"],
            "preprocessing_version": manifest["input_preprocessing_version"],
            "label_source": manifest["label_source"],
        }
        taxonomy = json.loads(args.taxonomy.read_text(encoding="utf-8-sig"))
        candidates = make_candidates(taxonomy)
        baselines = read_baselines(args.baselines_root, lineage, truth)
        full_validation_count = len(validation)
        if args.pilot_size:
            if args.pilot_size > len(validation):
                raise ValueError("Pilot size exceeds the validation partition.")
            validation, truth = validation[:args.pilot_size], truth[:args.pilot_size]
        print(f"Validation news: {len(validation)}", flush=True)
        if args.pilot_size:
            print(f"Technical pilot: first {args.pilot_size}/{full_validation_count} frozen validation records; metrics disabled.", flush=True)
        print("Training/fine-tuning: NO; train/test contents used: NO", flush=True)
        print("English taxonomy verified. Loading fixed model on CPU...", flush=True)
        import torch
        from gliclass import GLiClassModel, GLiClassModelConfig, ZeroShotClassificationPipeline
        from transformers import AutoTokenizer
        from huggingface_hub import try_to_load_from_cache
        torch.set_num_threads(args.threads)
        if importlib.metadata.version("gliclass") != "0.1.20":
            raise ValueError("This adapter was verified with gliclass==0.1.20.")
        tick = time.perf_counter()
        model_config = GLiClassModelConfig.from_pretrained(MODEL_ID, revision=REVISION)
        model_config.encoder_config.reference_compile = False
        if model_config.architecture_type != "uni-encoder" or not model_config.prompt_first:
            raise ValueError("Unexpected checkpoint input architecture.")
        model, loading_info = GLiClassModel.from_pretrained(
            MODEL_ID, revision=REVISION, config=model_config, output_loading_info=True)
        if any(loading_info.get(k) for k in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
            raise ValueError(f"Checkpoint loading is incomplete: {loading_info}.")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, add_prefix_space=True)
        model.to("cpu").eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        classifier = ZeroShotClassificationPipeline(model, tokenizer, device="cpu",
            classification_type="multi-label", max_length=MAX_LENGTH, progress_bar=False)
        if MAX_LENGTH > model.config.encoder_config.max_position_embeddings:
            raise ValueError("Requested input length exceeds encoder capacity.")
        model_load_seconds = time.perf_counter() - tick
        weight_path = try_to_load_from_cache(MODEL_ID, "model.safetensors", revision=REVISION)
        if not isinstance(weight_path, str) or not Path(weight_path).is_file():
            raise ValueError("Cannot verify the pinned checkpoint weight file.")
        weight_hash = file_hash(Path(weight_path))
        print("Model load and checkpoint keys: OK", flush=True)

        groups = pack_groups(candidates, classifier, tokenizer)
        chunks, owners, local_indices, article_chunks = [], [], [], []
        tick = time.perf_counter()
        for owner, row in enumerate(validation):
            spans = chunk_text(row["text"], tokenizer)
            article_chunks.append(spans)
            for local, span in enumerate(spans):
                chunks.append(span); owners.append(owner); local_indices.append(local)
        preparation_seconds = time.perf_counter() - tick
        environment = {"python": platform.python_version(), **{name: importlib.metadata.version(name)
            for name in ("numpy", "scikit-learn", "torch", "transformers", "gliclass", "huggingface-hub", "tokenizers")}}
        config = {
            "model_id": MODEL_ID, "revision": REVISION, "model_weights_sha256": weight_hash,
            "device": "cpu", "encoder_fine_tuned": False, "reference_compile": False,
            "classification_type": "multi-label", "score_type": "uncalibrated_sigmoid_max_over_chunks",
            "threshold": THRESHOLD, "prediction_rule": "score >= threshold", "thresholds_tuned": False,
            "max_sequence_tokens": MAX_LENGTH, "text_chunk_tokens": TEXT_TOKENS,
            "overlap_tokens": OVERLAP_TOKENS, "chunk_aggregation": "maximum",
            "candidate_format": "exact label name + fixed concise English summary of supplied taxonomy",
            "full_english_descriptions_sha256": DESCRIPTION_SHA256,
            "candidate_groups": [[LABEL_NAMES[i] for i in g] for g in groups],
            "candidate_texts": dict(zip(LABEL_NAMES, candidates)), "task_prompt": TASK_PROMPT,
            "few_shot_examples_used": False, "batch_size": args.batch_size, "threads": args.threads,
            "technical_pilot_size": args.pilot_size,
            "unknown_target": -1, "taxonomy_sha256": file_hash(args.taxonomy),
            "taxonomy_version": taxonomy["taxonomy_version"],
        }
        signature = {"model_version": MODEL_VERSION, "config": config, "lineage": lineage,
                     "environment": environment, "script_sha256": file_hash(Path(__file__)),
                     "baseline_report_hashes": {k: v["report_sha256"] for k, v in baselines.items()}}
        scores = np.full(truth.shape, np.nan)
        best_chunks = np.zeros(truth.shape, dtype=np.int32)
        completed, group_seconds, max_inputs = [], [], []
        if args.resume:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint["signature"] != signature:
                raise ValueError("Checkpoint data, taxonomy, model, code or environment differs.")
            completed = checkpoint["completed_groups"]
            if completed != list(range(len(completed))) or len(completed) > len(groups):
                raise ValueError("Checkpoint group order is invalid.")
            scores = np.asarray(checkpoint["scores"], dtype=float)
            best_chunks = np.asarray(checkpoint["best_chunks"], dtype=np.int32)
            if scores.shape != truth.shape or best_chunks.shape != truth.shape:
                raise ValueError("Checkpoint dimensions differ.")
            done_columns = [i for g in completed for i in groups[g]]
            undone_columns = [i for i in range(23) if i not in done_columns]
            if (not np.all(np.isfinite(scores[:, done_columns]))
                    or np.any((scores[:, done_columns] < 0) | (scores[:, done_columns] > 1))
                    or not np.all(np.isnan(scores[:, undone_columns]))
                    or np.any(best_chunks[:, done_columns] < 1)):
                raise ValueError("Checkpoint scores are inconsistent.")
            for owner, spans in enumerate(article_chunks):
                if np.any(best_chunks[owner, done_columns] > len(spans)):
                    raise ValueError("Checkpoint chunk indices are invalid.")
            group_seconds, max_inputs = checkpoint["group_seconds"], checkpoint["max_input_tokens"]
            if len(group_seconds) != len(completed) or len(max_inputs) != len(completed):
                raise ValueError("Checkpoint timing/coverage metadata is incomplete.")
            print(f"Resuming after {len(completed)}/{len(groups)} completed groups.", flush=True)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        def save_checkpoint():
            write_atomic(checkpoint_path, json_text({
                "signature": signature, "completed_groups": completed,
                "scores": [[float(v) if np.isfinite(v) else None for v in r] for r in scores],
                "best_chunks": best_chunks.tolist(), "group_seconds": group_seconds,
                "max_input_tokens": max_inputs,
            }))
        save_checkpoint()
        print(f"Validation chunks: {len(chunks)}; candidate groups: {len(groups)}", flush=True)
        print("All text retained; each full formatted input checked against truncation.", flush=True)
        for group_number in range(len(completed), len(groups)):
            group = groups[group_number]
            print(f"[{group_number + 1}/{len(groups)}] " + ", ".join(LABEL_NAMES[i] for i in group), flush=True)
            tick = time.perf_counter()
            group_scores, group_chunks, maximum = score_group(
                chunks, owners, local_indices, [candidates[i] for i in group],
                classifier, tokenizer, torch, len(validation), args.batch_size)
            scores[:, group] = group_scores
            best_chunks[:, group] = group_chunks
            completed.append(group_number)
            group_seconds.append(time.perf_counter() - tick)
            max_inputs.append(maximum)
            save_checkpoint()

        if args.pilot_size:
            config_hash = write_atomic(args.output_dir / "inference_config.json", json_text(config))
            pilot_predictions = [{
                "news_id": r["news_id"], "chunk_count": len(article_chunks[i]),
                "predicted_labels": [l for j, l in enumerate(LABEL_NAMES) if scores[i, j] >= THRESHOLD],
                "raw_score_by_label": {l: float(scores[i, j]) for j, l in enumerate(LABEL_NAMES)},
                "score_type": config["score_type"],
            } for i, r in enumerate(validation)]
            prediction_hash = write_atomic(args.output_dir / "pilot_predictions.jsonl", json_lines(pilot_predictions))
            pilot_report = {
                "schema_version": 1, "model_version": MODEL_VERSION,
                "run_type": "technical_pilot_no_model_selection_metrics",
                "config": config, "lineage": lineage, "environment": environment,
                "validation_news": len(validation), "full_validation_news": full_validation_count,
                "validation_chunks": len(chunks), "candidate_groups": len(groups),
                "all_input_characters_covered": True, "silent_truncation": False,
                "maximum_formatted_input_tokens": max(max_inputs),
                "metrics_computed": False, "encoder_fine_tuned": False,
                "train_contents_used": False, "test_contents_used": False,
                "model_load_seconds": model_load_seconds,
                "inference_seconds": sum(group_seconds),
                "elapsed_seconds": time.perf_counter() - started,
                "artifacts": {"inference_config_sha256": config_hash,
                              "pilot_predictions_sha256": prediction_hash},
                "note": "Pilot verifies runtime/coverage only. It is not a comparative accuracy evaluation.",
            }
            write_atomic(args.output_dir / "pilot_report.json", json_text(pilot_report))
            print("\nGLICLASS TECHNICAL PILOT COMPLETED")
            print("Pilot news:", len(validation))
            print("Validation chunks:", len(chunks))
            print("Scored labels: 23/23")
            print("Full text coverage: YES; silent truncation: NO")
            print("Encoder fine-tuned: NO; train/test contents used: NO")
            print("Inference seconds:", round(sum(group_seconds), 1))
            print("Model-selection metrics: NOT COMPUTED")
            print("Files saved to:", args.output_dir.resolve())
            return 0

        summary, per_label = evaluate(truth, scores)
        summary.update({"validation_chunks": len(chunks), "candidate_groups": len(groups),
                        "inference_seconds_including_tokenization": sum(group_seconds)})
        comparisons = {}
        ap_labels = [l for l in LABEL_NAMES if per_label[l]["metrics"]["average_precision"] is not None]
        for short, value in baselines.items():
            baseline = value["report"]
            for key in ("evaluated_known_pairs", "validation_unknown_pairs_excluded", "macro_f1_label_count"):
                if baseline["summary"][key] != summary[key]:
                    raise ValueError(f"Metric coverage differs: {short}/{key}.")
            if any(baseline["per_label"][l]["metrics"]["average_precision"] is None for l in ap_labels):
                raise ValueError(f"AP coverage differs: {short}.")
            comparisons[short] = {
                "baseline_model_version": baseline["model_version"], "baseline_report_sha256": value["report_sha256"],
                "baseline_micro": baseline["summary"]["validation_micro"],
                "baseline_macro_f1": baseline["summary"]["validation_macro_f1_positive_supported_labels"],
                "average_precision_labels": ap_labels,
                "baseline_macro_average_precision": float(np.mean([
                    baseline["per_label"][l]["metrics"]["average_precision"] for l in ap_labels])) if ap_labels else None,
                "gliclass_macro_average_precision": summary["validation_macro_average_precision"],
                "interpretation": "Identical validation known pairs. Fixed/default operating points; no matched-precision tuning and no winner selected.",
            }
        for label in LABEL_NAMES:
            support = baselines["tfidf_svm"]["report"]["per_label"][label]
            per_label[label].update({"baseline_train_positive": support["train_positive"],
                "low_baseline_training_positive_support": support["train_positive"] < 20})
        predicted = scores >= THRESHOLD
        predictions = [{
            "news_id": row["news_id"], "excel_row": row["excel_row"], "title": row["title"], "text": row["text"],
            "reference_shared_labels": row["shared_labels"], "reference_label_values": row["label_values"],
            "predicted_labels": [l for j, l in enumerate(LABEL_NAMES) if predicted[i, j]],
            "raw_score_by_label": {l: float(scores[i, j]) for j, l in enumerate(LABEL_NAMES)},
            "best_scoring_chunk_by_label": {l: int(best_chunks[i, j]) for j, l in enumerate(LABEL_NAMES)},
            "score_type": config["score_type"], "chunk_count": len(article_chunks[i]),
        } for i, row in enumerate(validation)]
        chunking_report = {
            "all_input_characters_covered": True, "silent_truncation": False,
            "maximum_formatted_input_tokens": max(max_inputs),
            "articles": [{"news_id": row["news_id"], "text_characters": len(row["text"]),
                          "chunks": [{k: c[k] for k in ("start", "end", "tokens")} for c in spans]}
                         for row, spans in zip(validation, article_chunks)],
            "best_chunk_is_verified_evidence": False,
        }
        inference_hash = write_atomic(args.output_dir / "inference_config.json", json_text(config))
        chunk_hash = write_atomic(args.output_dir / "chunking_report.json", json_text(chunking_report))
        prediction_hash = write_atomic(args.output_dir / "validation_predictions.jsonl", json_lines(predictions))
        report = {
            "schema_version": 1, "model_version": MODEL_VERSION, "labels": list(LABEL_NAMES),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "config": config, "environment": environment, "lineage": lineage,
            "taxonomy_source": taxonomy["source"], "validation_news": len(validation),
            "train_contents_used": False, "test_contents_used": False, "thresholds_tuned": False,
            "summary": summary, "per_label": per_label, "comparisons": comparisons,
            "model_load_seconds_this_invocation": model_load_seconds,
            "chunk_preparation_seconds_this_invocation": preparation_seconds,
            "group_inference_seconds": group_seconds, "resumed": args.resume,
            "elapsed_seconds_this_invocation": time.perf_counter() - started,
            "artifacts": {"validation_predictions_sha256": prediction_hash,
                          "inference_config_sha256": inference_hash, "chunking_report_sha256": chunk_hash},
            "limitations": [
                "Metrics measure consensus agreement, not human-reviewed ground truth.",
                "Unknown reference pairs are excluded; agreed negatives remain provisional.",
                "Scores and maximum chunk pooling are not calibrated VV probabilities or risk severity.",
                "Maximum pooling can increase false positives on longer articles.",
                "Relationships or qualifications spanning distant chunks may be missed.",
                "Definition length, candidate grouping and prompts can affect model scores.",
                "Concise candidate summaries do not encode every detailed exclusion or business-policy rule.",
                "Best-scoring chunks are retrieval candidates, not verified supporting evidence.",
                "Rare-label validity cannot be established from the available small validation support.",
                "Macro F1/AP exclude labels without suitable positive support.",
                "Matching label names does not establish identical historical annotation definitions.",
                "Repeated validation comparisons may overfit selection; test remains reserved.",
                "Experimental model; no production promotion or final winner selected.",
            ],
        }
        write_atomic(args.output_dir / "evaluation_report.json", json_text(report))
        micro = summary["validation_micro"]
        print("\nGLICLASS ZERO-SHOT EVALUATION COMPLETED", flush=True)
        print(f"Evaluated labels: {len(LABEL_NAMES)}/23; trained classifiers: 0")
        for metric in ("precision", "recall", "f1"):
            print(f"Validation micro {metric}: {format_metric(micro[metric])}")
        print("Validation macro F1:", format_metric(summary["validation_macro_f1_positive_supported_labels"]))
        print(f"Macro F1 label count: {summary['macro_f1_label_count']}/23")
        print("Macro average precision:", format_metric(summary["validation_macro_average_precision"]))
        for short, comparison in comparisons.items():
            print(f"Micro F1 ({short} -> GLiClass): "
                  f"{format_metric(comparison['baseline_micro']['f1'])} -> {format_metric(micro['f1'])}")
        print("Validation unknown pairs excluded:", summary["validation_unknown_pairs_excluded"])
        print(f"Validation chunks: {len(chunks)}; candidate groups: {len(groups)}")
        print("Full text coverage: YES; silent truncation: NO")
        print("Encoder fine-tuned: NO; train/test contents used: NO")
        print("Inference seconds:", round(sum(group_seconds), 1))
        print("Files saved to:", args.output_dir.resolve())
        print("Consensus agreement only. Fixed threshold; no winner selected.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted. Completed groups are preserved; rerun with --resume.", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
