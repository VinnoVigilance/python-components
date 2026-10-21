"""Per-label F1 thresholds from training-only out-of-fold decision scores."""
import time

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone
from sklearn.metrics import f1_score, precision_recall_curve
from sklearn.model_selection import KFold
from sklearn.utils.validation import check_is_fitted


def choose_f1_threshold(target, scores):
    """Search unique score cutoffs plus zero; ties prefer the cutoff nearest zero."""
    target, scores = np.asarray(target), np.asarray(scores, dtype=float)
    if (target.ndim != 1 or target.shape != scores.shape
            or not np.isin(target, [0, 1]).all() or not np.isfinite(scores).all()
            or not target.any() or target.all()):
        raise ValueError('Threshold selection requires finite scores and both target classes.')
    precision, recall, cutoffs = precision_recall_curve(target, scores)
    values = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-15)
    default_f1 = float(f1_score(target, scores >= 0, zero_division=0))
    cutoffs = np.append(cutoffs, 0.0)
    values = np.append(values, default_f1)
    best = np.flatnonzero(np.isclose(values, values.max(), rtol=0, atol=1e-12))
    selected = min(best, key=lambda i: (abs(cutoffs[i]), -cutoffs[i]))
    return float(cutoffs[selected]), default_f1, float(values[selected])


class OOFThresholdClassifier(ClassifierMixin, BaseEstimator):
    """Refit the whole feature/classifier pipeline inside every training fold.

    Final decision_function returns raw SVM margins. decision_thresholds_ is
    consumed by run.py and saved in the standard bundle for evaluate.py.
    Thresholds maximize per-label OOF F1; Validation and Test never enter fit.
    """

    def __init__(self, estimator, cv=3, random_state=42):
        self.estimator = estimator
        self.cv = cv
        self.random_state = random_state

    def fit(self, X, y):
        if isinstance(X, str):
            raise ValueError('Pass a list of article strings.')
        texts, targets = list(X), np.asarray(y)
        if (targets.ndim != 2 or len(targets) != len(texts)
                or not np.isin(targets, [0, 1]).all()):
            raise ValueError('Expected aligned texts and a binary multilabel matrix.')
        if not 2 <= self.cv <= len(texts):
            raise ValueError('cv must be between 2 and the number of training articles.')
        if ((targets.sum(axis=0) == 0) | (targets.sum(axis=0) == len(targets))).any():
            raise ValueError('Every training label needs positive and negative examples.')
        oof = np.full(targets.shape, np.nan, dtype=float)
        fold_ids = np.full(len(texts), -1, dtype=int)
        folds = []
        splitter = KFold(n_splits=self.cv, shuffle=True, random_state=self.random_state)
        for fold, (fit_ids, holdout_ids) in enumerate(splitter.split(texts), 1):
            support = targets[fit_ids].sum(axis=0)
            if ((support == 0) | (support == len(fit_ids))).any():
                raise ValueError(f'Fold {fold} has a label missing one training class.')
            print(f'OOF fold {fold}/{self.cv}: fit {len(fit_ids)} articles, '
                  f'score {len(holdout_ids)} held-out articles', flush=True)
            started = time.perf_counter()
            fold_model = clone(self.estimator)
            fold_model.fit([texts[i] for i in fit_ids], targets[fit_ids])
            fold_scores = np.asarray(fold_model.decision_function([texts[i] for i in holdout_ids]))
            if fold_scores.shape != targets[holdout_ids].shape:
                raise ValueError('Unexpected OOF decision-score shape.')
            if (fold_ids[holdout_ids] != -1).any():
                raise ValueError('A training article received multiple OOF predictions.')
            oof[holdout_ids], fold_ids[holdout_ids] = fold_scores, fold
            folds.append({'fold': fold, 'fit_rows': len(fit_ids), 'holdout_rows': len(holdout_ids),
                          'fit_positive_support': support.tolist(),
                          'holdout_positive_support': targets[holdout_ids].sum(axis=0).tolist(),
                          'seconds': time.perf_counter() - started})
            print(f"OOF fold {fold}/{self.cv} finished in {folds[-1]['seconds']:.2f} s", flush=True)
            del fold_model
        if (fold_ids < 1).any() or not np.isfinite(oof).all():
            raise ValueError('OOF predictions are incomplete or non-finite.')
        reports = []
        for j in range(targets.shape[1]):
            threshold, default, tuned = choose_f1_threshold(targets[:, j], oof[:, j])
            reports.append({'threshold': threshold, 'default_threshold': 0.0,
                            'train_support': int(targets[:, j].sum()),
                            'oof_f1_default': default, 'oof_f1_selected': tuned,
                            'oof_predicted_positive_default': int((oof[:, j] >= 0).sum()),
                            'oof_predicted_positive_selected': int((oof[:, j] >= threshold).sum())})
        self.decision_thresholds_ = np.asarray([r['threshold'] for r in reports])
        self.threshold_policy_ = (f'per-label F1 optimized on {self.cv}-fold Train OOF scores; '
                                  'no Validation/Test threshold tuning')
        self.threshold_report_, self.oof_scores_, self.oof_fold_ids_ = reports, oof, fold_ids
        self.tuning_summary_ = {
            'source_split': 'train', 'cv': self.cv, 'splitter': 'KFold(shuffle=True)',
            'random_state': self.random_state, 'objective': 'per-label F1 (Macro-F1 separable)',
            'candidate_policy': 'all unique OOF scores plus 0; inclusive >= decision rule',
            'tie_policy': 'nearest zero, then higher threshold',
            'feature_fit': 'fresh complete TF-IDF + SVM pipeline inside each fold',
            'oof_macro_f1_default': float(np.mean([r['oof_f1_default'] for r in reports])),
            'oof_macro_f1_selected': float(np.mean([r['oof_f1_selected'] for r in reports])),
            'oof_score_note': 'used to choose thresholds; not an independent quality estimate',
            'folds': folds,
        }
        print('Thresholds selected from Train OOF scores. Refitting on all Train articles...', flush=True)
        self.estimator_ = clone(self.estimator).fit(texts, targets)
        self.classes_ = np.arange(targets.shape[1])
        return self

    def decision_function(self, X):
        check_is_fitted(self, 'estimator_')
        return self.estimator_.decision_function(X)

    def predict(self, X):
        return (np.asarray(self.decision_function(X)) >= self.decision_thresholds_).astype(np.int8)
