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
            "risk_details_hash": "same-hash", "version_no": 2,
        }
        with patch("services.risk.processing.calculate_record_hash", return_value="same-hash"):
            outcome = _process(self.connection, self.repository, self.item,
                               "incremental", self.classify)
        self.assertEqual(outcome, "skipped")
        self.repository.expire_current.assert_not_called()
        self.repository.insert_risk.assert_not_called()

    def test_delete_expires_without_loading_classifier(self):
        self.item["action"] = "DELETE"
        outcome = _process(self.connection, self.repository, self.item,
                           "incremental", self.classify)
        self.assertEqual(outcome, "deleted")
        self.classify.assert_not_called()
        self.repository.expire_current.assert_called_once_with(self.cursor, "member-1")
        self.repository.insert_risk.assert_not_called()

    def test_superseded_delta_does_not_replace_current_assessment(self):
        self.item["source_record_id"] = 204
        outcome = _process(self.connection, self.repository, self.item,
                           "incremental", self.classify)
        self.assertEqual(outcome, "superseded")
        self.classify.assert_not_called()
        self.repository.expire_current.assert_not_called()

    def test_reprocess_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "initial or incremental"):
            run("watchlist", self.repository, 802156604, mode="reprocess")


if __name__ == "__main__":
    unittest.main()
