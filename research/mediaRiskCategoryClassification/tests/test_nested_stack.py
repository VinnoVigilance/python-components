"""Train holdout isolation, feature cross-fitting and persisted stack outputs."""
from pathlib import Path
import sys
import tempfile
import unittest

import joblib
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import LABELS
from models._nested_stack import NestedTextStack, checked_scores


class FrozenVectors(TransformerMixin, BaseEstimator):
    def __init__(self, torch_threads=1):
        self.torch_threads = torch_threads

    def fit(self, X, y=None):
        if y is not None:
            raise AssertionError('Frozen encoder received targets.')
        return self

    def transform(self, X):
        z = np.asarray([int(x) for x in X], dtype=float)
        return np.c_[z, np.sin(z), np.cos(z)]

    def runtime_metadata(self):
        return {'frozen': True, 'saw_targets': False}


class RecordedMargins(BaseEstimator):
    calls = []

    def __init__(self, semantic=False):
        self.semantic = semantic

    def _ids(self, X):
        return np.asarray(X)[:, 0].astype(int) if self.semantic else np.array([int(x) for x in X])

    def fit(self, X, y):
        self.fit_ids_ = self._ids(X)
        self.means_ = np.mean(y, axis=0)
        self.classes_ = np.arange(len(LABELS))
        return self

    def decision_function(self, X):
        ids = self._ids(X)
        type(self).calls.append((self.fit_ids_.copy(), ids.copy()))
        signal = np.sin(ids[:, None] * np.arange(1, len(LABELS) + 1)[None, :] * .1)
        return signal * (.1 if self.semantic else .2) + self.means_


class StackTests(unittest.TestCase):
    def inputs(self):
        x = [str(i) for i in range(90)]
        y = (np.random.default_rng(142).random((90, len(LABELS))) < .35).astype(np.int8)
        y[0] = 0
        return x, y

    def model(self):
        return NestedTextStack(RecordedMargins(), FrozenVectors(),
                               RecordedMargins(True), torch_threads=1)

    def test_all_supervised_base_scores_are_cross_fitted_and_save_restores(self):
        x, y = self.inputs()
        RecordedMargins.calls = []
        m = self.model().fit(x, y)
        # 3 outer * (3 inner + 1 outer holdout), plus 3 final inner;
        # each makes 2 base predictions. All must exclude scored rows.
        self.assertEqual(len(RecordedMargins.calls), 30)
        for fit, hold in RecordedMargins.calls:
            self.assertFalse(set(fit) & set(hold))
        self.assertEqual(m.oof_scores_.shape, (90, len(LABELS)))
        self.assertEqual(set(m.oof_fold_ids_), {1, 2, 3})
        self.assertEqual(m.base_train_oof_.shape, (90, len(LABELS), 2))
        self.assertEqual(m.training_summary_['all_zero_target_rows'], int((y.sum(axis=1) == 0).sum()))
        self.assertGreaterEqual(m.training_summary_['all_zero_target_rows'], 1)
        self.assertFalse(m.training_summary_['validation_or_test_used_for_fit'])
        self.assertTrue(np.isfinite(m.oof_scores_).all())
        scores = m.decision_function(x[:4])
        expected = (scores >= m.decision_thresholds_).astype(np.int8)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'stack.joblib'
            joblib.dump(m, p)
            restored = joblib.load(p)
            self.assertFalse(hasattr(restored, 'last_base_scores_'))
            np.testing.assert_array_equal(restored.predict(x[:4]), expected)

    def test_changing_outer_holdout_targets_cannot_change_its_scores(self):
        x, y = self.inputs()
        first = self.model().fit(x, y)
        hold = first.oof_fold_ids_ == 1
        changed = y.copy()
        changed[hold] = 1 - changed[hold]
        second = self.model().fit(x, changed)
        np.testing.assert_array_equal(first.oof_scores_[hold], second.oof_scores_[hold])

    def test_bad_inputs_and_scores_are_rejected(self):
        x, y = self.inputs()
        with self.assertRaises(ValueError):
            self.model().fit(x, np.full_like(y, -1))
        with self.assertRaises(ValueError):
            self.model().fit(x, y[:, :-1])
        with self.assertRaises(ValueError):
            checked_scores(RecordedMargins().fit(x, y), x, 89)


if __name__ == '__main__':
    unittest.main()
