"""Inference for the original NB-SVM Joblib; no research package imports."""
from __future__ import annotations

import json
import platform
import re
import unicodedata
from pathlib import Path

import joblib
import numpy as np
import scipy
import sklearn
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.svm import LinearSVC

from intelligence.risk.mediaRiskCategory.contract import (
    MediaRiskModelError,
    load_label_contract,
)


class _ThresholdState:
    """Saved research wrapper state, without importing its training code."""


class _NBSVMState:
    """Saved research classifier state, without importing its training code."""


def _load_original(path: Path) -> dict:
    # Isolate the historical class-name mapping inside one reader; do not change
    # sys.path, sys.modules, joblib globals, or the original model file.
    # These Joblib container APIs are version-coupled: the environment check below
    # requires the exact Joblib version recorded by the model's own run.json.
    from joblib.numpy_pickle import NumpyUnpickler, _validate_fileobject_and_memmap

    class ResearchReader(NumpyUnpickler):
        def find_class(self, module, name):
            known = {
                ("models._oof_thresholds", "OOFThresholdClassifier"): _ThresholdState,
                ("models._nbsvm", "NBSVMClassifier"): _NBSVMState,
            }
            return known[(module, name)] if (module, name) in known else super().find_class(module, name)

    with path.open("rb") as raw:
        with _validate_fileobject_and_memmap(raw, str(path), None) as (stream, _):
            if isinstance(stream, str):
                raise MediaRiskModelError("Unsupported legacy Joblib container format.")
            return ResearchReader(str(path), stream, ensure_native_byte_order=True).load()


def _check_environment(expected: dict) -> None:
    actual = {"python": platform.python_version(), "numpy": np.__version__,
              "scipy": scipy.__version__, "scikit_learn": sklearn.__version__, "joblib": joblib.__version__}
    for name, installed in actual.items():
        required = expected.get(name)
        matches = required == installed
        if name == "python":
            matches = bool(required) and required.split(".")[:2] == installed.split(".")[:2]
        if not matches:
            raise MediaRiskModelError(
                f"{name}: model requires {required}; installed {installed}. "
                "Use the same environment as the selected research run."
            )


def prepare_text(title: str | None, body: str | None) -> str | None:
    """Input is plain text; reproduce the frozen research preparation policy."""
    for field, value in (("title", title), ("body", body)):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{field} must be a string or None.")
    def clean(value):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()
    title, body = clean(title), clean(body)
    return (title + "\n\n" + body).strip() if body else None


def load_model(directory: Path) -> tuple[dict, dict]:
    try:
        for name in ("model.joblib", "run.json"):
            if not (directory / name).is_file():
                raise MediaRiskModelError(f"Missing {directory / name}. Copy it from the selected run.")
        metadata = json.loads((directory / "run.json").read_text(encoding="utf-8-sig"))
        contract = load_label_contract(directory)
        if (metadata.get("status") != "completed" or metadata.get("model_name") != "nbsvm_oof"
                or metadata.get("labels") != list(contract.artifact_labels)
                or metadata.get("run_id") != contract.run_id):
            raise MediaRiskModelError("Completed nbsvm_oof run.json does not match manifest.json.")
        _check_environment(metadata["environment"])
        source = _load_original(directory / "model.joblib")
        if (source["labels"] != list(contract.artifact_labels) or source["score_method"] != "decision_function"
                or source["dataset_id"] != metadata["dataset_id"]
                or source["protocol_id"] != metadata["protocol_id"]):
            raise MediaRiskModelError("model.joblib and run.json do not describe the same model.")
        wrapper = source["model"]
        if not isinstance(wrapper, _ThresholdState):
            raise MediaRiskModelError("Unexpected research model wrapper.")
        steps = wrapper.estimator_.named_steps
        vectorizer, classifier = steps["counts"], steps["classifier"]
        if (not isinstance(vectorizer, CountVectorizer) or not vectorizer.binary
                or vectorizer.ngram_range != (1, 2) or not isinstance(classifier, _NBSVMState)):
            raise MediaRiskModelError("Unexpected NB-SVM feature pipeline.")
        thresholds = np.asarray(source["thresholds"], dtype=float)
        if (not np.array_equal(thresholds, wrapper.decision_thresholds_)
                or not np.array_equal(thresholds, metadata["thresholds"])):
            raise MediaRiskModelError("Saved model thresholds do not match run.json.")
        ratios = np.asarray(classifier.log_count_ratios_, dtype=float)
        classifiers = classifier.estimators_
        width, count = len(vectorizer.vocabulary_), len(contract.label_ids)
        if (thresholds.shape != (count,) or not np.isfinite(thresholds).all()
                or ratios.shape != (count, width) or not np.isfinite(ratios).all()
                or len(classifiers) != count):
            raise MediaRiskModelError("Invalid model dimensions or non-finite parameters.")
        for svm in classifiers:
            if (not isinstance(svm, LinearSVC) or svm.n_features_in_ != width
                    or not np.array_equal(svm.classes_, [0, 1])
                    or svm.coef_.shape != (1, width) or not np.isfinite(svm.coef_).all()
                    or not np.isfinite(svm.intercept_).all()):
                raise MediaRiskModelError("Invalid fitted binary SVM.")
        model = {"vectorizer": vectorizer, "classifiers": classifiers,
                 "ratios": ratios, "thresholds": thresholds,
                 "label_contract": contract}
        return model, metadata
    except MediaRiskModelError:
        raise
    except Exception as error:
        raise MediaRiskModelError(f"Cannot load model from {directory}: {error}") from error


def decision_scores(model: dict, texts: list[str]) -> np.ndarray:
    count = len(model["label_contract"].label_ids)
    if not texts:
        return np.empty((0, count), dtype=float)
    features = model["vectorizer"].transform(texts)
    scores = np.column_stack([
        svm.decision_function(features.multiply(ratio).tocsr())
        for svm, ratio in zip(model["classifiers"], model["ratios"])
    ])
    if scores.shape != (len(texts), count) or not np.isfinite(scores).all():
        raise MediaRiskModelError("Model returned invalid decision scores.")
    return scores
