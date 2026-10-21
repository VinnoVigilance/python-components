"""Canonical media payload adapter for the packaged NB-SVM model."""

import hashlib
from pathlib import Path

from intelligence.risk.mediaRiskCategory.predictor import MediaRiskPredictor
from intelligence.risk.result import calculation_version, implementation_version


class MediaRiskClassifier:
    """Return an assessment suitable for versioned media risk storage."""

    def __init__(self, model_path=None):
        self.predictor = MediaRiskPredictor(model_path)
        base = Path(__file__).parent / "mediaRiskCategory"
        self.runtime_version = implementation_version([
            __file__, base / "predictor.py", base / "nbsvm.py", base / "contract.py",
            base.parent / "labelContract.py", base.parent / "result.py",
        ])
        self.policy_version = "sha256:" + hashlib.sha256(
            self.predictor._model["thresholds"].tobytes()
        ).hexdigest()
        self.calculation_version = calculation_version(
            self.predictor.artifact_version, self.policy_version, self.runtime_version,
        )

    def classify(self, payload):
        content = payload.get("Content") or {}
        prediction = self.predictor.classify(content.get("Title"), content.get("BodyText"))
        decisions = {item["riskCategoryId"]: item for item in prediction["labelDecisions"]}
        risk_categories = []
        for label_id, name in zip(prediction["label_ids"], prediction["labels"]):
            decision = decisions[label_id]
            risk_categories.append({
                "riskCategoryId": label_id,
                "riskCategoryName": name,
                "method": "ml",
                "score": decision["score"],
                "scoreType": decision["scoreType"],
                "threshold": decision["threshold"],
                "explanation": "",
                "metadata": {
                    "modelVersion": prediction["model_version"],
                    "calculationVersion": self.calculation_version,
                },
            })
        return {
            "assessmentStatus": prediction["status"],
            "riskCategories": sorted(risk_categories, key=lambda item: item["riskCategoryId"]),
            "reviewRequired": [],
            "taxonomyVersion": prediction["taxonomy_version"],
        }
