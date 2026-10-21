"""Check watchlist label routing without loading model weights."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from intelligence.risk.watchlistRiskClassifier import WatchlistRiskClassifier


class WatchlistAutoLabelTests(unittest.TestCase):
    def setUp(self):
        self.classifier = object.__new__(WatchlistRiskClassifier)
        self.classifier.labels = {
            "Law Enforcement": ("LE_GEN", "Law Enforcement"),
            "Crime-Fraud": ("CR_FRAUD", "Crime-Fraud"),
            "PEP": ("PEP_GEN", "PEP"),
        }
        self.classifier.student = SimpleNamespace(
            policy={
                "auto_apply_text_label_ids": ["CR_FRAUD"],
                "text_threshold": 0.5,
                "victim_threshold": 0.5,
                "context_chars": 5,
                "victim_record_chars": 1200,
            },
            score=MagicMock(),
            model_version="watchlist-risk-e5small-v6:weights",
            manifest={"model_id": "watchlist-risk-e5small-v6"},
            contract=SimpleNamespace(taxonomy_version="sha256:taxonomy"),
        )
        self.classifier.triggers = {}
        self.classifier.calculation_version = "sha256:calculation"
        self.classifier.rules_version = "sha256:rules"
        self.classifier.lookup_calculation_version = "sha256:lookup"
        self.payload = {"Sources": [{"ListName": "TEST-LIST"}]}

    def test_auto_label_keeps_highest_score_and_all_evidence(self):
        self.classifier.student.score.return_value = [0.8, 0.9, 0.7, 0.1]
        units = [("Remarks", "first fraud"), ("Measures", "second fraud"),
                 ("Occupation", "political office")]
        candidates = [(0, "Crime-Fraud", ""), (1, "Crime-Fraud", ""),
                      (2, "PEP", "")]
        with patch("intelligence.risk.watchlistRiskClassifier.watchlistText.render", return_value="text"), \
                patch("intelligence.risk.watchlistRiskClassifier.highlighter.split_units", return_value=units), \
                patch("intelligence.risk.watchlistRiskClassifier.highlighter.flag_units", return_value=candidates), \
                patch("intelligence.risk.watchlistRiskClassifier.lookupRules.record_rule_labels",
                      return_value=["Law Enforcement"]):
            result = self.classifier.classify(self.payload)

        categories = {item["riskCategoryId"]: item for item in result["riskCategories"]}
        self.assertEqual(set(categories), {"LE_GEN", "CR_FRAUD"})
        self.assertEqual(categories["CR_FRAUD"]["score"], 0.9)
        self.assertEqual(len(categories["CR_FRAUD"]["evidence"]), 2)
        self.assertEqual([item["riskCategoryId"] for item in result["reviewRequired"]], ["PEP_GEN"])
        for item in result["riskCategories"] + result["reviewRequired"]:
            self.assertEqual(item["explanation"], "")
            self.assertIn("calculationVersion", item["metadata"])
        self.assertNotIn("labelMetadata", result)
        self.assertNotIn("metadata", result)
        self.assertEqual(categories["CR_FRAUD"]["metadata"]["modelVersion"],
                         self.classifier.student.model_version)

    def test_lookup_label_is_not_replaced_by_model_result(self):
        self.classifier.student.score.return_value = [0.8, 0.1]
        with patch("intelligence.risk.watchlistRiskClassifier.watchlistText.render", return_value="text"), \
                patch("intelligence.risk.watchlistRiskClassifier.highlighter.split_units",
                      return_value=[("Remarks", "fraud")]), \
                patch("intelligence.risk.watchlistRiskClassifier.highlighter.flag_units",
                      return_value=[(0, "Crime-Fraud", "")]), \
                patch("intelligence.risk.watchlistRiskClassifier.lookupRules.record_rule_labels",
                      return_value=["Crime-Fraud"]):
            result = self.classifier.classify(self.payload)

        self.assertEqual(len(result["riskCategories"]), 1)
        label = result["riskCategories"][0]
        self.assertEqual(label["riskCategoryId"], "CR_FRAUD")
        self.assertEqual(label["method"], "lookup")
        self.assertEqual(label["metadata"]["rulesVersion"], "sha256:rules")
        self.assertNotIn("modelVersion", label["metadata"])
        self.assertEqual(result["reviewRequired"], [])

    def test_victim_cutoff_still_blocks_text_labels_but_preserves_lookup(self):
        self.classifier.student.score.return_value = [0.9, 0.5]
        with patch("intelligence.risk.watchlistRiskClassifier.watchlistText.render", return_value="text"), \
                patch("intelligence.risk.watchlistRiskClassifier.highlighter.split_units",
                      return_value=[("Remarks", "fraud")]), \
                patch("intelligence.risk.watchlistRiskClassifier.highlighter.flag_units",
                      return_value=[(0, "Crime-Fraud", "")]), \
                patch("intelligence.risk.watchlistRiskClassifier.lookupRules.record_rule_labels",
                      return_value=["Law Enforcement"]):
            result = self.classifier.classify(self.payload)
        self.assertEqual([item["riskCategoryId"] for item in result["riskCategories"]], ["LE_GEN"])
        self.assertEqual(result["reviewRequired"], [])


if __name__ == "__main__":
    unittest.main()
