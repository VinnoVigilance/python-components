"""Standalone media NB-SVM predictions with label-level diagnostics."""

import hashlib
import os
from pathlib import Path

from intelligence.risk.mediaRiskCategory.nbsvm import decision_scores, load_model, prepare_text


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_DIR = ROOT_DIR / "intelligence/risk/mediaRiskCategory/models/nbsvm-v1"


class MediaRiskPredictor:
    """Load once and score normalized title/body pairs."""

    def __init__(self, model_path=None):
        directory = Path(model_path or os.getenv("MEDIA_RISK_MODEL_DIR") or DEFAULT_MODEL_DIR).expanduser()
        if not directory.is_absolute():
            directory = ROOT_DIR / directory
        self._model, self._metadata = load_model(directory.resolve())
        digest = hashlib.sha256()
        for name in ("manifest.json", "run.json", "model.joblib"):
            with (directory / name).open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
        self.artifact_version = "sha256:" + digest.hexdigest()

    @property
    def model_version(self):
        return self._model["label_contract"].model_id + ":" + self._metadata["run_id"]

    @property
    def supported_labels(self):
        return self._model["label_contract"].display_labels

    @property
    def supported_label_ids(self):
        return self._model["label_contract"].label_ids

    @property
    def taxonomy_version(self):
        return self._model["label_contract"].taxonomy_version

    def classify(self, title, body):
        return self.classify_many([(title, body)])[0]

    def classify_many(self, articles):
        """Preserve input order, including skipped articles."""
        texts = [prepare_text(title, body) for title, body in articles]
        positions = [index for index, text in enumerate(texts) if text is not None]
        scores = decision_scores(self._model, [texts[index] for index in positions])
        results = [{"status": "SKIPPED_EMPTY_BODY", "stage": "ml",
                    "model_version": self.model_version, "taxonomy_version": self.taxonomy_version,
                    "labels": [], "label_ids": [], "labelDecisions": []} for _ in texts]
        for index, values in zip(positions, scores):
            decisions = [
                {"riskCategoryId": label_id, "evaluated": True, "score": float(value),
                 "scoreType": "svm_margin", "threshold": float(cutoff),
                 "comparison": ">=", "selected": bool(value >= cutoff)}
                for label_id, value, cutoff in zip(self.supported_label_ids, values, self._model["thresholds"])
            ]
            selected = [position for position, decision in enumerate(decisions) if decision["selected"]]
            results[index] = {"status": "CLASSIFIED", "stage": "ml", "model_version": self.model_version,
                              "taxonomy_version": self.taxonomy_version,
                              "labels": [self.supported_labels[position] for position in selected],
                              "label_ids": [self.supported_label_ids[position] for position in selected],
                              "labelDecisions": decisions}
        return results
