"""Broad fast-classical baseline screen with fixed stratified group folds."""

import argparse
import csv
import hashlib
import json
import math
import platform
import time
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import scipy
import sklearn
from scipy.sparse import hstack
from sklearn.base import clone
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.linear_model import LogisticRegression, PassiveAggressiveClassifier, Perceptron, RidgeClassifier, SGDClassifier
from sklearn.metrics import (accuracy_score, average_precision_score, balanced_accuracy_score,
                             f1_score, matthews_corrcoef, precision_score, recall_score,
                             confusion_matrix)
from sklearn.naive_bayes import BernoulliNB, ComplementNB, GaussianNB, MultinomialNB
from sklearn.neighbors import KNeighborsClassifier, NearestCentroid
from sklearn.preprocessing import normalize
from sklearn.decomposition import TruncatedSVD
from sklearn.svm import LinearSVC, SVC
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.tree import DecisionTreeClassifier

from contracts import file_digest


SEED = 115
FOLDS = 5
DEFAULT_DATA = Path("research/adverseNews/data/labeling/final-pilot-500-v2/training-pool-500-title-body-labels.jsonl")
DEFAULT_OUTPUT = Path("research/adverseNews/results/classical-screen-v1")


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(path, rows, columns):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def tfidf_word():
    return TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_df=0.98,
                           max_features=60000, sublinear_tf=True, strip_accents="unicode",
                           dtype=np.float32)


def tfidf_char():
    return TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2,
                           max_features=120000, sublinear_tf=True,
                           dtype=np.float32)


def count_word():
    return CountVectorizer(ngram_range=(1, 2), min_df=2, max_df=0.98,
                           max_features=60000, dtype=np.float32)


def candidate_specs():
    specs = [{"name": "dummy::majority", "features": "none", "kind": "dummy", "params": {}}]

    for c in (0.1, 1.0, 10.0):
        specs.append({"name": f"word_tfidf::logistic_C{c:g}", "features": "word", "kind": "logistic", "params": {"C": c}})
        specs.append({"name": f"word_tfidf::linear_svm_C{c:g}", "features": "word", "kind": "linear_svm", "params": {"C": c}})
        specs.append({"name": f"hybrid_tfidf::logistic_C{c:g}", "features": "hybrid", "kind": "logistic", "params": {"C": c}})
        specs.append({"name": f"hybrid_tfidf::linear_svm_C{c:g}", "features": "hybrid", "kind": "linear_svm", "params": {"C": c}})
    for c in (0.5, 2.0):
        specs.append({"name": f"word_tfidf::rbf_svm_C{c:g}", "features": "word", "kind": "rbf_svm", "params": {"C": c}})
    for alpha in (1e-5, 1e-4, 1e-3):
        specs.append({"name": f"word_tfidf::sgd_hinge_a{alpha:g}", "features": "word", "kind": "sgd_hinge", "params": {"alpha": alpha}})
        specs.append({"name": f"word_tfidf::sgd_logloss_a{alpha:g}", "features": "word", "kind": "sgd_logloss", "params": {"alpha": alpha}})
    for alpha in (0.1, 1.0, 10.0):
        specs.append({"name": f"word_tfidf::ridge_a{alpha:g}", "features": "word", "kind": "ridge", "params": {"alpha": alpha}})
    for c in (0.1, 1.0):
        specs.append({"name": f"word_tfidf::passive_aggressive_C{c:g}", "features": "word", "kind": "passive_aggressive", "params": {"C": c}})
    specs.append({"name": "word_tfidf::perceptron", "features": "word", "kind": "perceptron", "params": {}})

    for alpha in (0.1, 1.0):
        for kind in ("multinomial_nb", "complement_nb", "bernoulli_nb"):
            specs.append({"name": f"word_tfidf::{kind}_a{alpha:g}", "features": "word", "kind": kind, "params": {"alpha": alpha}})
    for depth in (20, None):
        specs.append({"name": f"word_tfidf::decision_tree_depth{depth}", "features": "word", "kind": "decision_tree", "params": {"max_depth": depth}})
    specs.extend([
        {"name": "word_tfidf::random_forest_200", "features": "word", "kind": "random_forest", "params": {"n_estimators": 200}},
        {"name": "word_tfidf::extra_trees_200", "features": "word", "kind": "extra_trees", "params": {"n_estimators": 200}},
        {"name": "word_tfidf::nearest_centroid", "features": "word", "kind": "nearest_centroid", "params": {}},
    ])
    for k in (3, 7):
        specs.append({"name": f"word_tfidf::knn_cosine_k{k}", "features": "word", "kind": "knn", "params": {"n_neighbors": k}})

    for features in ("char", "hybrid"):
        for kind, suffix in (("logistic", "logistic_C1"), ("linear_svm", "linear_svm_C1"),
                             ("sgd_hinge", "sgd_hinge"), ("passive_aggressive", "passive_aggressive"),
                             ("complement_nb", "complement_nb"), ("nbsvm", "nbsvm")):
            params = {"C": 1.0} if kind in ("logistic", "linear_svm", "passive_aggressive") else {}
            specs.append({"name": f"{features}_tfidf::{suffix}", "features": features, "kind": kind, "params": params})
    specs.append({"name": "char_tfidf::rbf_svm_C1", "features": "char", "kind": "rbf_svm", "params": {"C": 1.0}})
    specs.extend([
        {"name": "word_count::multinomial_nb_a0.1", "features": "count", "kind": "multinomial_nb", "params": {"alpha": 0.1}},
        {"name": "word_count::complement_nb_a0.1", "features": "count", "kind": "complement_nb", "params": {"alpha": 0.1}},
        {"name": "word_count::bernoulli_nb_a0.1", "features": "count", "kind": "bernoulli_nb", "params": {"alpha": 0.1}},
    ])
    specs.extend([
        {"name": "lsa100::logistic_C1", "features": "lsa", "kind": "logistic", "params": {"C": 1.0}},
        {"name": "lsa100::linear_svm_C1", "features": "lsa", "kind": "linear_svm", "params": {"C": 1.0}},
        {"name": "lsa100::rbf_svm_C1", "features": "lsa", "kind": "rbf_svm", "params": {"C": 1.0}},
        {"name": "lsa100::decision_tree_depth5", "features": "lsa", "kind": "decision_tree", "params": {"max_depth": 5}},
        {"name": "lsa100::random_forest_200", "features": "lsa", "kind": "random_forest", "params": {"n_estimators": 200}},
        {"name": "lsa100::extra_trees_200", "features": "lsa", "kind": "extra_trees", "params": {"n_estimators": 200}},
        {"name": "lsa100::hist_gradient_boosting", "features": "lsa", "kind": "hist_gradient_boosting", "params": {}},
        {"name": "lsa100::gaussian_nb", "features": "lsa", "kind": "gaussian_nb", "params": {}},
    ])
    unique, seen = [], set()
    for spec in specs:
        if spec["name"] not in seen:
            unique.append(spec)
            seen.add(spec["name"])
    return unique


def make_model(spec, seed):
    kind, p = spec["kind"], spec["params"]
    if kind == "dummy":
        return DummyClassifier(strategy="most_frequent")
    if kind == "logistic":
        return LogisticRegression(C=p.get("C", 1.0), solver="liblinear", max_iter=2000,
                                  class_weight="balanced", random_state=seed)
    if kind == "linear_svm":
        return LinearSVC(C=p.get("C", 1.0), class_weight="balanced", max_iter=12000, random_state=seed)
    if kind == "rbf_svm":
        return SVC(C=p.get("C", 1.0), kernel="rbf", gamma="scale", class_weight="balanced")
    if kind == "sgd_hinge":
        return SGDClassifier(loss="hinge", alpha=p.get("alpha", 1e-4), max_iter=3000,
                             tol=1e-4, class_weight="balanced", random_state=seed)
    if kind == "sgd_logloss":
        return SGDClassifier(loss="log_loss", alpha=p.get("alpha", 1e-4), max_iter=3000,
                             tol=1e-4, class_weight="balanced", random_state=seed)
    if kind == "ridge":
        return RidgeClassifier(alpha=p.get("alpha", 1.0), class_weight="balanced")
    if kind == "passive_aggressive":
        # sklearn 1.8 deprecated the dedicated class; this is its documented
        # SGD equivalent and keeps the same linear PA-I update family.
        return SGDClassifier(loss="hinge", penalty=None, learning_rate="pa1", eta0=1.0,
                             alpha=0.0, max_iter=3000, tol=1e-4,
                             class_weight="balanced", random_state=seed)
    if kind == "perceptron":
        return Perceptron(max_iter=3000, tol=1e-4, class_weight="balanced", random_state=seed)
    if kind == "multinomial_nb":
        return MultinomialNB(alpha=p.get("alpha", 1.0))
    if kind == "complement_nb":
        return ComplementNB(alpha=p.get("alpha", 1.0))
    if kind == "bernoulli_nb":
        return BernoulliNB(alpha=p.get("alpha", 1.0), binarize=0.0)
    if kind == "decision_tree":
        return DecisionTreeClassifier(max_depth=p.get("max_depth"), min_samples_leaf=2,
                                      class_weight="balanced", random_state=seed)
    if kind == "random_forest":
        return RandomForestClassifier(n_estimators=p.get("n_estimators", 200), max_features="sqrt",
                                      min_samples_leaf=2, class_weight="balanced_subsample",
                                      n_jobs=1, random_state=seed)
    if kind == "extra_trees":
        return ExtraTreesClassifier(n_estimators=p.get("n_estimators", 200), max_features="sqrt",
                                    min_samples_leaf=2, class_weight="balanced",
                                    n_jobs=1, random_state=seed)
    if kind == "nearest_centroid":
        return NearestCentroid()
    if kind == "knn":
        return KNeighborsClassifier(n_neighbors=p.get("n_neighbors", 5), weights="distance",
                                    metric="cosine", algorithm="brute", n_jobs=1)
    if kind == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=15, l2_regularization=1.0,
                                              class_weight="balanced", random_state=seed)
    if kind == "gaussian_nb":
        return GaussianNB()
    if kind == "nbsvm":
        return NBSVM(C=p.get("C", 1.0), seed=seed)
    raise ValueError(f"unknown estimator kind: {kind}")


class NBSVM:
    """Binary Naive Bayes log-count ratio reweighting followed by a linear SVM."""
    def __init__(self, C=1.0, seed=SEED):
        self.C, self.seed = C, seed

    def fit(self, X, y):
        present = X.copy().tocsr()
        present.data[:] = 1.0
        y = np.asarray(y)
        positive = np.asarray(present[y == 1].sum(axis=0)).ravel() + 1.0
        negative = np.asarray(present[y == 0].sum(axis=0)).ravel() + 1.0
        self.r_ = np.log(positive / positive.sum()) - np.log(negative / negative.sum())
        self.model_ = LinearSVC(C=self.C, class_weight="balanced", max_iter=12000, random_state=self.seed)
        self.model_.fit(X.multiply(self.r_), y)
        return self

    def decision_function(self, X):
        return self.model_.decision_function(X.multiply(self.r_))

    def predict(self, X):
        return (self.decision_function(X) >= 0).astype(int)


def vectorize_fold(train_text, valid_text, seed, lsa_components=100):
    word = tfidf_word()
    Xw_train = word.fit_transform(train_text)
    Xw_valid = word.transform(valid_text)
    char = tfidf_char()
    Xc_train = char.fit_transform(train_text)
    Xc_valid = char.transform(valid_text)
    count = count_word()
    Xn_train = count.fit_transform(train_text)
    Xn_valid = count.transform(valid_text)
    hybrid_train = hstack([Xw_train, Xc_train], format="csr", dtype=np.float32)
    hybrid_valid = hstack([Xw_valid, Xc_valid], format="csr", dtype=np.float32)
    components = min(lsa_components, Xw_train.shape[0] - 1, Xw_train.shape[1] - 1)
    svd = TruncatedSVD(n_components=components, n_iter=5, random_state=seed)
    lsa_train = svd.fit_transform(hybrid_train)
    lsa_valid = svd.transform(hybrid_valid)
    lsa_train = normalize(lsa_train).astype(np.float32, copy=False)
    lsa_valid = normalize(lsa_valid).astype(np.float32, copy=False)
    return {"word": (Xw_train, Xw_valid), "char": (Xc_train, Xc_valid),
            "hybrid": (hybrid_train, hybrid_valid), "count": (Xn_train, Xn_valid),
            "lsa": (lsa_train, lsa_valid)}, {
                "word_features": Xw_train.shape[1], "char_features": Xc_train.shape[1],
                "count_features": Xn_train.shape[1], "hybrid_features": hybrid_train.shape[1],
                "lsa_components": components, "lsa_explained_variance": float(svd.explained_variance_ratio_.sum())}


def get_scores(model, X, y_pred):
    if hasattr(model, "decision_function"):
        score = np.asarray(model.decision_function(X), dtype=float).ravel()
        kind = "decision_function"
    elif hasattr(model, "predict_proba"):
        proba = np.asarray(model.predict_proba(X), dtype=float)
        classes = list(model.classes_)
        score = proba[:, classes.index(1)]
        kind = "positive_probability_un-calibrated"
    elif hasattr(model, "centroids_"):
        from sklearn.metrics.pairwise import euclidean_distances
        distances = euclidean_distances(X, model.centroids_)
        classes = list(model.classes_)
        score = -distances[:, classes.index(1)] + distances[:, classes.index(0)]
        kind = "negative_centroid_distance_margin"
    else:
        score = y_pred.astype(float)
        kind = "hard_prediction_only"
    return score, kind


def metrics(y_true, y_pred, score):
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {"accuracy": accuracy_score(y_true, y_pred),
            "balanced_accuracy": balanced_accuracy_score(y_true, y_pred),
            "precision_positive": precision_score(y_true, y_pred, zero_division=0),
            "recall_positive": recall_score(y_true, y_pred, zero_division=0),
            "f1_positive": f1_score(y_true, y_pred, zero_division=0),
            "mcc": matthews_corrcoef(y_true, y_pred),
            "average_precision": average_precision_score(y_true, score),
            "false_negative_rate": fn / (fn + tp) if fn + tp else 0.0,
            "false_omission_rate": fn / (fn + tn) if fn + tn else 0.0,
            "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("output directory exists; choose a new run path")
    rows = read_jsonl(args.data)
    if len(rows) != 500 or any(type(r["label"]) is not int or r["label"] not in (0, 1) for r in rows):
        raise ValueError("expected the completed 500-row binary pool")
    texts = [(r["title"] + "\n\n" + r["body"]).strip() for r in rows]
    y = np.asarray([r["label"] for r in rows], dtype=int)
    groups = np.asarray([r["automatic_group_id"] for r in rows])
    sources = np.asarray([r["source_name"] for r in rows])
    if len(set(groups)) != len(groups):
        raise ValueError("automatic duplicate groups are not unique; inspect fold/group assignment")
    splitter = StratifiedGroupKFold(n_splits=FOLDS, shuffle=True, random_state=SEED)
    split_list = list(splitter.split(np.zeros(len(y)), y, groups))
    for i, (train, valid) in enumerate(split_list):
        if set(groups[train]) & set(groups[valid]):
            raise ValueError(f"group leakage in fold {i + 1}")
        if set(y[train]) != {0, 1} or set(y[valid]) != {0, 1}:
            raise ValueError(f"fold {i + 1} does not contain both labels")

    specs = candidate_specs()
    output = args.output
    output.mkdir(parents=True)
    model_rows, fold_rows, oof_rows, failures, feature_rows = [], [], [], [], []
    all_started = time.perf_counter()
    for fold_number, (train_idx, valid_idx) in enumerate(split_list, 1):
        vector_started = time.perf_counter()
        matrices, feature_meta = vectorize_fold([texts[i] for i in train_idx],
                                                [texts[i] for i in valid_idx], SEED + fold_number)
        vector_seconds = time.perf_counter() - vector_started
        feature_rows.append({"fold": fold_number, "train_n": len(train_idx), "valid_n": len(valid_idx),
                             "train_positive": int(y[train_idx].sum()), "valid_positive": int(y[valid_idx].sum()),
                             "vectorize_seconds": vector_seconds, **feature_meta})
        print(f"Fold {fold_number}/{FOLDS}: features ready; {len(train_idx)} train / {len(valid_idx)} held out")
        for spec in specs:
            model = make_model(spec, SEED + fold_number)
            X_train, X_valid = matrices.get(spec["features"], (None, None))
            start = time.perf_counter()
            caught = []
            try:
                with warnings.catch_warnings(record=True) as recorded:
                    warnings.simplefilter("always")
                    model.fit(X_train if X_train is not None else np.zeros((len(train_idx), 1)), y[train_idx])
                    predictions = model.predict(X_valid if X_valid is not None else np.zeros((len(valid_idx), 1))).astype(int)
                    scores, score_kind = get_scores(model, X_valid if X_valid is not None else np.zeros((len(valid_idx), 1)), predictions)
                    caught = [f"{type(w.message).__name__}: {w.message}" for w in recorded]
                elapsed = time.perf_counter() - start
                result = metrics(y[valid_idx], predictions, scores)
                fold_rows.append({"model": spec["name"], "features": spec["features"], "kind": spec["kind"],
                                  "fold": fold_number, "train_n": len(train_idx), "valid_n": len(valid_idx),
                                  "train_positive": int(y[train_idx].sum()), "valid_positive": int(y[valid_idx].sum()),
                                  "fit_predict_seconds": elapsed, "vectorize_seconds_shared": vector_seconds,
                                  "score_kind": score_kind, **result})
                for local, idx in enumerate(valid_idx):
                    oof_rows.append({"model": spec["name"], "fold": fold_number,
                                     "request_article_id": rows[idx]["request_article_id"],
                                     "physical_article_id": rows[idx]["physical_article_id"],
                                     "source_name": sources[idx], "label_origin": rows[idx]["label_origin"],
                                     "y_true": int(y[idx]), "y_pred": int(predictions[local]),
                                     "score": float(scores[local]), "score_kind": score_kind})
                for message in caught:
                    failures.append({"model": spec["name"], "fold": fold_number, "warning": message})
            except Exception as error:
                failures.append({"model": spec["name"], "fold": fold_number,
                                 "error": f"{type(error).__name__}: {error}"})
                print(f"  FAILED {spec['name']} fold {fold_number}: {type(error).__name__}: {error}")
        print(f"Fold {fold_number}/{FOLDS}: {len(specs)} candidate configs processed")

    by_model_oof = defaultdict(list)
    by_model_folds = defaultdict(list)
    for row in oof_rows:
        by_model_oof[row["model"]].append(row)
    for row in fold_rows:
        by_model_folds[row["model"]].append(row)
    summary_rows = []
    for spec in specs:
        runs = by_model_folds[spec["name"]]
        oof = by_model_oof[spec["name"]]
        if len(runs) != FOLDS or len(oof) != len(rows):
            failures.append({"model": spec["name"], "fold": None,
                             "error": f"incomplete OOF coverage: folds={len(runs)}, rows={len(oof)}"})
            continue
        yy = np.asarray([r["y_true"] for r in oof])
        pp = np.asarray([r["y_pred"] for r in oof])
        ss = np.asarray([r["score"] for r in oof])
        pooled = metrics(yy, pp, ss)
        summary_rows.append({"model": spec["name"], "features": spec["features"], "kind": spec["kind"],
                             "params": json.dumps(spec["params"], sort_keys=True),
                             "mean_fold_f1": float(np.mean([r["f1_positive"] for r in runs])),
                             "std_fold_f1": float(np.std([r["f1_positive"] for r in runs], ddof=1)),
                             "mean_fold_recall": float(np.mean([r["recall_positive"] for r in runs])),
                             "std_fold_recall": float(np.std([r["recall_positive"] for r in runs], ddof=1)),
                             "mean_fold_precision": float(np.mean([r["precision_positive"] for r in runs])),
                             "mean_fold_ap": float(np.mean([r["average_precision"] for r in runs])),
                             "std_fold_ap": float(np.std([r["average_precision"] for r in runs], ddof=1)),
                             "mean_fold_balanced_accuracy": float(np.mean([r["balanced_accuracy"] for r in runs])),
                             "pooled_oof_f1": pooled["f1_positive"], "pooled_oof_precision": pooled["precision_positive"],
                             "pooled_oof_recall": pooled["recall_positive"], "pooled_oof_ap": pooled["average_precision"],
                             "pooled_oof_mcc": pooled["mcc"], "pooled_oof_fnr": pooled["false_negative_rate"],
                             "pooled_oof_for": pooled["false_omission_rate"], "pooled_oof_tn": pooled["tn"],
                             "pooled_oof_fp": pooled["fp"], "pooled_oof_fn": pooled["fn"], "pooled_oof_tp": pooled["tp"],
                             "fit_predict_seconds_total": float(sum(r["fit_predict_seconds"] for r in runs))})
    summary_rows.sort(key=lambda r: (-r["mean_fold_f1"], -r["mean_fold_ap"], -r["mean_fold_recall"]))

    write_csv(output / "summary.csv", summary_rows, list(summary_rows[0]) if summary_rows else ["model"])
    write_csv(output / "fold-metrics.csv", fold_rows, list(fold_rows[0]) if fold_rows else ["model"])
    write_csv(output / "oof-predictions.csv", oof_rows, list(oof_rows[0]) if oof_rows else ["model"])
    write_csv(output / "fold-features.csv", feature_rows, list(feature_rows[0]) if feature_rows else ["fold"])
    write_csv(output / "warnings-and-failures.csv", failures, ["model", "fold", "warning", "error"])

    elapsed = time.perf_counter() - all_started
    manifest = {"schema_version": "adverse-classical-screen-v1", "status": "completed",
                "data_file": str(args.data), "data_sha256": file_digest(args.data),
                "runner_sha256": file_digest(Path(__file__)), "python": platform.python_version(),
                "numpy": np.__version__, "scipy": scipy.__version__, "scikit_learn": sklearn.__version__,
                "groups": "automatic_group_id", "group_count": len(set(groups)),
                "same_case_review_pending": True, "folds": FOLDS, "fold_strategy": "StratifiedGroupKFold(shuffle=True, random_state=115)",
                "fold_membership": [{"fold": i + 1, "train_ids": [rows[j]["request_article_id"] for j in tr],
                                      "heldout_ids": [rows[j]["request_article_id"] for j in va]}
                                     for i, (tr, va) in enumerate(split_list)],
                "semantic_features": ["title", "body"], "metadata_excluded_from_features": True,
                "label_counts": {str(k): int(v) for k, v in Counter(y).items()},
                "label_origin_counts": dict(Counter(r["label_origin"] for r in rows)),
                "candidates_configured": len(specs), "candidates_completed": len(summary_rows),
                "failed_or_incomplete_candidates": len({r["model"] for r in failures if r.get("error")}),
                "elapsed_seconds": elapsed,
                "selection_warning": "Broad candidate screening can overfit fold noise; rankings are exploratory and require source-held-out and independent human-reference evaluation.",
                "production_accuracy_claim": False}
    (output / "run-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Classical model screen", "",
             f"- Data: {len(rows)} teacher-/human-adjudicated articles; positive={int(y.sum())}, negative={len(y)-int(y.sum())}.",
             f"- Cross-validation: {FOLDS}-fold stratified group CV over {len(set(groups))} automatic text groups.",
             f"- Candidate configs: {len(summary_rows)} completed / {len(specs)} planned.",
             f"- Elapsed seconds: {elapsed:.1f}.",
             "- All vectorizers and SVD were fitted inside each training fold; model inputs were title/body only.",
             "- Same-case/near-duplicate review is pending, so groups and estimates are provisional.",
             "- Labels are largely teacher-derived; CV measures fit to these targets, not production truth.",
             "", "## Top 15 by mean fold F1", "",
             "| Model | Features | F1 mean ± SD | Recall | Precision | AP | OOF F1 | OOF AP | OOF FN | OOF FP |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in summary_rows[:15]:
        lines.append(f"| {row['model']} | {row['features']} | {row['mean_fold_f1']:.3f} ± {row['std_fold_f1']:.3f} | {row['mean_fold_recall']:.3f} | {row['mean_fold_precision']:.3f} | {row['mean_fold_ap']:.3f} | {row['pooled_oof_f1']:.3f} | {row['pooled_oof_ap']:.3f} | {row['pooled_oof_fn']} | {row['pooled_oof_fp']} |")
    lines.extend(["", "Full rankings are in `summary.csv`; per-fold behavior is in `fold-metrics.csv`; one out-of-fold prediction per article/model is in `oof-predictions.csv`.", ""])
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"candidate_count": len(specs), "completed": len(summary_rows),
                      "failed": len({r["model"] for r in failures if r.get("error")}),
                      "elapsed_seconds": round(elapsed, 1),
                      "top5": [{"model": r["model"], "mean_fold_f1": round(r["mean_fold_f1"], 4),
                                "mean_fold_ap": round(r["mean_fold_ap"], 4), "mean_fold_recall": round(r["mean_fold_recall"], 4)}
                               for r in summary_rows[:5]]}, indent=2))


if __name__ == "__main__":
    main()
