import unittest
from unittest.mock import patch

from intelligence.risk.watchlistRiskCategory.lookupRules import record_rule_labels
from transforms.fieldMapper import MappingEngine, load_rules


class WatchlistStructuredLabelsTests(unittest.TestCase):
    def test_idb_mapping_splits_practices_and_preserves_other_information(self):
        mapper = MappingEngine(load_rules("data/rules/mapping.xlsx", "IDB_SANCTIONED_ENTITIES"))
        for entity_type in ("Individual", "Entity"):
            with self.subTest(entity_type=entity_type):
                raw = {"entity_type": entity_type, "source_record_id": "example",
                       "Title": "Example", "Prohibited Practice": "Fraud, Corruption",
                       "Source": "IDB"}
                record = mapper.map_record(raw)
                values = {(item["Type"], item["Value"]) for item in record["additionalInfo"]}
                self.assertIn(("Prohibited Practice", "Fraud"), values)
                self.assertIn(("Prohibited Practice", "Corruption"), values)
                self.assertIn(("Sanction Source", "IDB"), values)
                labels = record_rule_labels("IDB_SANCTIONED_ENTITIES", record)
                self.assertTrue({"Crime-Fraud", "Crime-Corruption"} <= labels.keys())

    def test_idb_practices_are_individual_values(self):
        record = {
            "Sources": [{"DatasetCategory": ["Sanctions"]}],
            "additionalInfo": [
                {"Type": "Prohibited Practice", "Value": "Fraud"},
                {"Type": "Prohibited Practice", "Value": "Corruption"},
                {"Type": "Prohibited Practice", "Value": "Collusion"},
                {"Type": "Sanction Source", "Value": "Fraud"},
            ],
        }
        labels = record_rule_labels("IDB_SANCTIONED_ENTITIES", record)
        self.assertEqual(labels, {
            "Sanctions": "dataset category Sanctions",
            "Crime-Fraud": "additionalInfo.Prohibited Practice: Fraud",
            "Crime-Corruption": "additionalInfo.Prohibited Practice: Corruption",
        })

    def test_combined_value_is_not_split_by_classifier(self):
        record = {"additionalInfo": [{"Type": "Prohibited Practice", "Value": "Fraud, Corruption"}]}
        self.assertEqual(record_rule_labels("IDB_SANCTIONED_ENTITIES", record), {})

    def test_dnfbp_does_not_apply_dataset_category(self):
        record = {"Sources": [{"DatasetCategory": ["Regulatory"]}]}
        self.assertEqual(record_rule_labels("DNFBP", record), {})

    def test_multiple_categories_and_legacy_string(self):
        record = {"Sources": [{"DatasetCategory": ["Sanctions", "PEP"]},
                              {"DatasetCategory": "Sanctions"}]}
        self.assertEqual(set(record_rule_labels("UNREVIEWED", record)), {"Sanctions", "PEP"})

    def test_structured_rule_supports_another_object_array(self):
        config = {"structured_risk_mappings": [{"field": "Events", "value_key": "Outcome",
                   "labels": {"Fraud": "Crime-Fraud"}}]}
        with patch.dict("intelligence.risk.watchlistRiskCategory.lookupRules.WATCHLIST_CONFIGS",
                        {"CUSTOM": config}):
            labels = record_rule_labels("CUSTOM", {"Events": [{"Outcome": "Fraud"}]})
        self.assertEqual(labels, {"Crime-Fraud": "Events.Outcome: Fraud"})


if __name__ == "__main__":
    unittest.main()
