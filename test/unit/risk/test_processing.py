"""Check risk assessment replacement decisions without a database."""

import unittest
from unittest.mock import MagicMock, patch

from services.risk.processing import _process, run


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.repository = MagicMock()
        self.item = {"id": 205, "subject_id": "member-1", "source_record_id": 205,
                     "action": "UPDATE"}
        self.source = {"id": 205, "subject_id": "member-1", "version_no": 2,
                       "change_type": "UPDATED", "payload": {"name": "Example"}}
        self.repository.find_current_source.return_value = self.source
        self.repository.find_source_version.return_value = self.source
        self.classify = MagicMock(return_value={"assessmentStatus": "CLASSIFIED",
                                                  "riskCategories": [], "reviewRequired": []})

    def test_same_result_on_new_source_version_creates_assessment(self):
        self.repository.find_current_risk.return_value = {
            "risk_details_hash": "same-hash", "version_no": 1,
        }
        with patch("services.risk.processing.calculate_record_hash", return_value="same-hash"):
            outcome = _process(self.connection, self.repository, self.item,
                               "incremental", self.classify)
        self.assertEqual(outcome, "versioned")
        self.repository.expire_current.assert_called_once_with(self.cursor, "member-1")
        self.repository.insert_risk.assert_called_once()

    def test_identical_result_and_source_version_skips(self):
        self.repository.find_current_risk.return_value = {
            "risk_details_hash": "same-hash", "version_no": 2, "source_record_id": 205,
        }
        with patch("services.risk.processing.calculate_record_hash", return_value="same-hash"):
            outcome = _process(self.connection, self.repository, self.item,
                               "incremental", self.classify)
        self.assertEqual(outcome, "skipped")
        self.repository.expire_current.assert_not_called()
        self.repository.insert_risk.assert_not_called()

    def test_delete_expires_without_loading_classifier(self):
        self.item["action"] = "DELETE"
        self.repository.find_current_risk.return_value = {"version_no": 2}
        outcome = _process(self.connection, self.repository, self.item,
                           "incremental", self.classify)
        self.assertEqual(outcome, "deleted")
        self.classify.assert_not_called()
        self.repository.expire_current.assert_called_once_with(self.cursor, "member-1")
        self.repository.insert_risk.assert_not_called()

    def test_historical_delta_is_classified_without_replacing_current_assessment(self):
        self.item["source_record_id"] = 204
        self.repository.find_source_version.return_value = {**self.source, "id": 204, "version_no": 1}
        self.repository.has_assessment.return_value = False
        outcome = _process(self.connection, self.repository, self.item,
                           "incremental", self.classify)
        self.assertEqual(outcome, "historical")
        self.classify.assert_called_once()
        self.repository.expire_current.assert_not_called()
        self.assertFalse(self.repository.insert_risk.call_args.kwargs["current"])

    def test_historical_delete_preserves_current_assessment(self):
        self.item["action"] = "DELETE"
        self.repository.find_source_version.return_value = {**self.source, "id": 204, "version_no": 1}
        self.repository.find_current_risk.return_value = {"version_no": 2}
        self.assertEqual(_process(self.connection, self.repository, self.item,
                                  "incremental", self.classify), "deleted")
        self.repository.expire_current.assert_not_called()
        self.classify.assert_not_called()

    def test_matching_historical_result_skips_insertion(self):
        self.repository.find_source_version.return_value = {**self.source, "id": 204, "version_no": 1}
        self.repository.has_assessment.return_value = True
        self.assertEqual(_process(self.connection, self.repository, self.item,
                                  "incremental", self.classify), "skipped")
        self.repository.insert_risk.assert_not_called()

    def test_mismatched_source_fails_instead_of_classifying_other_subject(self):
        self.repository.find_source_version.return_value = {**self.source, "subject_id": "other"}
        with self.assertRaises(LookupError):
            _process(self.connection, self.repository, self.item, "incremental", self.classify)
        self.classify.assert_not_called()

    def test_reprocess_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "initial or incremental"):
            run("watchlist", self.repository, 802156604, mode="reprocess")


if __name__ == "__main__":
    unittest.main()
