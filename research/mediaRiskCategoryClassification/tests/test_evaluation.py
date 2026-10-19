"""Small hand-calculated cases for shared metrics and data separation."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from config import LABELS
from data import check_separation
from evaluation import classify, metrics, score_model


class EvaluationTests(unittest.TestCase):
    def test_false_positives_on_all_zero_rows_count(self):
        y = np.zeros((3, len(LABELS)), dtype=int)
        y[0, 0], y[1, 1] = 1, 1
        scores = np.full(y.shape, -2.0)
        scores[0, 0], scores[2, 0] = 2, 2
        summary, details, _ = metrics(y, scores, np.zeros(len(LABELS)))
        self.assertEqual((details[0]['tp'], details[0]['fp'], details[1]['fn']), (1, 1, 1))
        self.assertAlmostEqual(summary['micro_precision'], 0.5)
        self.assertAlmostEqual(summary['micro_recall'], 0.5)
        self.assertAlmostEqual(summary['micro_f1'], 0.5)
        self.assertAlmostEqual(summary['exact_match'], 1 / 3)
        self.assertEqual(summary['all_zero_target_rows'], 1)

    def test_probability_and_margin_thresholds(self):
        np.testing.assert_array_equal(classify([[0.6, 0.4]], [0.5, 0.5]), [[1, 0]])
        np.testing.assert_array_equal(classify([[0.1, -0.1]], [0.0, 0.0]), [[1, 0]])

    def test_score_contract_rejects_wrong_label_dimension(self):
        class BrokenModel:
            def predict_proba(self, texts):
                return np.ones((len(texts), 2))
        with self.assertRaises(ValueError):
            score_model(BrokenModel(), ['one article'])

    def test_duplicate_body_with_different_id_is_leakage(self):
        first = [{'news_id': 'one', 'body_hash': 'same'}]
        second = [{'news_id': 'two', 'body_hash': 'same'}]
        with self.assertRaises(ValueError):
            check_separation(first, second)


if __name__ == '__main__':
    unittest.main()
