"""Check inclusive thresholds, training-fold isolation and runner persistence contract."""
from collections import Counter
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import LABELS
import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.metrics import f1_score
from models._oof_thresholds import OOFThresholdClassifier, choose_f1_threshold
from models.char_tfidf_svm import build as baseline
from models.char_svm_unweighted import build as unweighted
from run import trained_thresholds


class UnseenOnlyEstimator(ClassifierMixin, BaseEstimator):
    fits = []
    predictions = []

    def fit(self, X, y):
        self.seen_ = set(X)
        type(self).fits.append(self.seen_.copy())
        return self

    def decision_function(self, X):
        if self.seen_.intersection(X):
            raise AssertionError('An OOF prediction used a training article.')
        type(self).predictions.extend(X)
        values = np.asarray([int(x) for x in X])
        return np.column_stack([values % 5 - 1., values % 7 - 2.])


class OOFTests(unittest.TestCase):
    def test_selected_cutoff_handles_ties_and_matches_brute_force(self):
        y = np.array([1, 0, 1, 0])
        scores = np.array([.8, .8, -.4, -.9])
        threshold, default, selected = choose_f1_threshold(y, scores)
        expected = max(f1_score(y, scores >= t) for t in np.append(np.unique(scores), 0))
        self.assertAlmostEqual(threshold, -.4)
        self.assertAlmostEqual(selected, expected)
        self.assertGreater(selected, default)
        self.assertEqual(int((scores >= threshold).sum()), 3)

    def test_equal_f1_keeps_default_zero(self):
        threshold, default, selected = choose_f1_threshold([1, 0], [.8, -.8])
        self.assertEqual((threshold, default, selected), (0., 1., 1.))

    def test_every_oof_row_is_scored_once_by_a_model_that_did_not_fit_it(self):
        UnseenOnlyEstimator.fits, UnseenOnlyEstimator.predictions = [], []
        texts = [str(i) for i in range(18)]
        y = np.column_stack([np.arange(18) % 2, (np.arange(18) % 3 == 0).astype(int)])
        model = OOFThresholdClassifier(UnseenOnlyEstimator(), cv=3).fit(texts, y)
        self.assertEqual(Counter(UnseenOnlyEstimator.predictions), Counter(texts))
        self.assertEqual([len(x) for x in UnseenOnlyEstimator.fits], [12, 12, 12, 18])
        np.testing.assert_array_equal(np.bincount(model.oof_fold_ids_)[1:], [6, 6, 6])
        np.testing.assert_array_equal(model.predict(['100', '101']),
                                      model.decision_function(['100', '101']) >= model.decision_thresholds_)
        self.assertFalse(hasattr(clone(model), 'estimator_'))

    def test_unweighted_changes_only_one_baseline_parameter(self):
        old, new = baseline().get_params(), unweighted().get_params()
        changes = {k for k in old if isinstance(old[k], (str, int, float, bool, type(None)))
                   and old[k] != new[k]}
        self.assertEqual(changes, {'classifier__estimator__class_weight'})
        self.assertIsNone(new['classifier__estimator__class_weight'])

    def test_runner_uses_learned_thresholds_and_rejects_wrong_shape(self):
        class Learned:
            decision_thresholds_ = np.linspace(-1, 1, len(LABELS))
        np.testing.assert_array_equal(trained_thresholds(Learned(), 'decision_function'),
                                      Learned.decision_thresholds_)
        Learned.decision_thresholds_ = np.zeros(len(LABELS) - 1)
        with self.assertRaises(ValueError):
            trained_thresholds(Learned(), 'decision_function')


if __name__ == '__main__':
    unittest.main()
