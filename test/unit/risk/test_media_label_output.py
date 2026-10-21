"""Verify selected media labels own their provenance and explanation."""

from unittest.mock import MagicMock

from intelligence.risk.mediaRiskClassifier import MediaRiskClassifier
from intelligence.risk.result import build_assessment_payload


def test_media_metadata_is_nested_only_on_emitted_labels():
    classifier = object.__new__(MediaRiskClassifier)
    classifier.calculation_version = "sha256:calculation"
    classifier.predictor = MagicMock()
    classifier.predictor.classify.return_value = {
        "status": "CLASSIFIED", "label_ids": ["CR_FRAUD"], "labels": ["Crime-Fraud"],
        "labelDecisions": [{"riskCategoryId": "CR_FRAUD", "score": 0.8,
                            "scoreType": "svm_margin", "threshold": 0.1}],
        "model_version": "media-model", "taxonomy_version": "sha256:taxonomy",
    }
    result = build_assessment_payload(classifier.classify({"Content": {"BodyText": "example"}}))
    label = result["riskCategories"][0]
    assert label["explanation"] == ""
    assert label["metadata"] == {"modelVersion": "media-model", "calculationVersion": "sha256:calculation"}
    assert result["reviewRequired"] == []
    assert result["schemaVersion"] == "2.0"
    assert "metadata" not in result
    assert "labelMetadata" not in result
